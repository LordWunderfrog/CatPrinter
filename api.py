"""
HTTP API for the YHK cat printer. Intended for LAN / HA / reverse-proxy callers.

  GET  /health   — process up (no auth, no BT)
  GET  /ready    — RFCOMM probe; 503 if sleepy/unreachable (no auth)
  GET  /status   — same probe as /ready but always HTTP 200 (HA sensors)
  POST /printer/wake — best-effort BT nudge + RFCOMM probe (no auth)
  POST /print/text      JSON: {"text": "...", "font_size": 65}
  POST /print/markdown  JSON: {"markdown": "..."}
  POST /print/image     multipart form field "file" (image)
  POST /print/reddit    JSON: {"subreddit": "..."}  # optional; DEFAULT_SUBREDDIT

Env (in addition to yhk_printer):
  API_HOST, API_PORT, API_TOKEN, DEFAULT_SUBREDDIT
  MAX_TEXT_CHARS, MAX_MARKDOWN_CHARS, MAX_UPLOAD_BYTES, MAX_IMAGE_PIXELS

Auth (only if API_TOKEN is set): send header
  X-Api-Key: <token>
  or Authorization: Bearer <token>
NFC / HA: same header on every print call. /health, /ready, /status, /printer/wake stay open.
"""
from __future__ import annotations

import io
import logging
import os
import secrets
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager

import PIL.Image
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from image_prep import prepare_raster_image
from markdown_renderer import render_markdown
from reddit_image import RedditImageError, fetch_random_subreddit_image
from yhk_printer import get_config, print_image, print_text, printer_session

_print_lock = threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("cat_printer.api")

# Crash-level ceilings only — normal receipts/photos sail under these.
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", str(50_000)))
MAX_MARKDOWN_CHARS = int(os.environ.get("MAX_MARKDOWN_CHARS", str(100_000)))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", str(25_000_000)))
READY_TIMEOUT_S = float(os.environ.get("READY_TIMEOUT_S", "5"))
WAKE_BLUETOOTHCTL = os.environ.get("WAKE_BLUETOOTHCTL", "1").strip() not in (
    "0",
    "false",
    "no",
)


def _api_token() -> str:
    return (os.environ.get("API_TOKEN") or "").strip()


def _default_subreddit() -> str:
    return (os.environ.get("DEFAULT_SUBREDDIT") or "chonkers").strip() or "chonkers"


class TextPrintRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    font_size: int = Field(65, ge=8, le=200)


class MarkdownPrintRequest(BaseModel):
    markdown: str = Field(..., min_length=1, max_length=MAX_MARKDOWN_CHARS)


class RedditPrintRequest(BaseModel):
    subreddit: str | None = Field(None, min_length=1, max_length=64)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_config()
    token_on = bool(_api_token())
    log.info(
        "event=startup printer_mac=%s printer_port=%s auth=%s default_subreddit=%s",
        cfg["mac"],
        cfg["port"],
        "on" if token_on else "off",
        _default_subreddit(),
    )
    yield


app = FastAPI(title="Cat Printer", version="1.0.0", lifespan=lifespan)


def _extract_token(
    authorization: str | None,
    x_api_key: str | None,
) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


@app.middleware("http")
async def auth_and_request_log(request: Request, call_next):
    req_id = uuid.uuid4().hex[:8]
    request.state.req_id = req_id
    started = time.perf_counter()

    path = request.url.path
    if path.startswith("/print"):
        expected = _api_token()
        if expected:
            got = _extract_token(
                request.headers.get("authorization"),
                request.headers.get("x-api-key"),
            )
            if not got or not secrets.compare_digest(got, expected):
                log.warning(
                    "event=auth_denied req_id=%s path=%s client=%s",
                    req_id,
                    path,
                    request.client.host if request.client else "-",
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized — send X-Api-Key or Authorization: Bearer"},
                )

    response = await call_next(request)
    ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "event=request req_id=%s method=%s path=%s status=%s duration_ms=%s",
        req_id,
        request.method,
        path,
        response.status_code,
        ms,
    )
    return response


@app.get("/health")
def health():
    """Liveness: process is up. Does not touch Bluetooth."""
    cfg = get_config()
    return {
        "ok": True,
        "printer_mac": cfg["mac"],
        "printer_port": cfg["port"],
        "auth_required": bool(_api_token()),
        "default_subreddit": _default_subreddit(),
    }


def _bluetoothctl_nudge(mac: str) -> str | None:
    """Best-effort Classic reconnect. Returns a short note or None if skipped."""
    if not WAKE_BLUETOOTHCTL:
        return None
    try:
        subprocess.run(
            ["bluetoothctl", "disconnect", mac],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        time.sleep(1.0)
        proc = subprocess.run(
            ["bluetoothctl", "connect", mac],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        log.info(
            "event=wake_bluetoothctl mac=%s returncode=%s out=%r",
            mac,
            proc.returncode,
            out[:200],
        )
        return out[:200] if out else f"bluetoothctl exit {proc.returncode}"
    except FileNotFoundError:
        log.info("event=wake_bluetoothctl skipped reason=not_installed")
        return "bluetoothctl not installed"
    except Exception as e:
        log.warning("event=wake_bluetoothctl_fail mac=%s error=%s", mac, e)
        return str(e)


def _probe_printer(*, timeout: float) -> dict:
    """
    RFCOMM probe under the print lock.
    Returns a payload dict with keys: ok, printer, printer_mac, and optional detail.
    printer is awake|busy|sleepy|error.
    """
    cfg = get_config()
    if not _print_lock.acquire(blocking=False):
        log.info("event=probe printer=busy mac=%s", cfg["mac"])
        return {"ok": True, "printer": "busy", "printer_mac": cfg["mac"]}

    try:
        try:
            with printer_session(probe=True, timeout=timeout):
                pass
        except OSError as e:
            log.warning("event=probe printer=sleepy mac=%s error=%s", cfg["mac"], e)
            return {
                "ok": False,
                "printer": "sleepy",
                "printer_mac": cfg["mac"],
                "detail": str(e),
            }
        except Exception as e:
            log.warning("event=probe printer=error mac=%s error=%s", cfg["mac"], e)
            return {
                "ok": False,
                "printer": "error",
                "printer_mac": cfg["mac"],
                "detail": str(e),
            }
        log.info("event=probe printer=awake mac=%s", cfg["mac"])
        return {"ok": True, "printer": "awake", "printer_mac": cfg["mac"]}
    finally:
        _print_lock.release()


@app.get("/ready")
def ready():
    """
    Readiness: can we open RFCOMM to the printer right now?
    200 awake / busy; 503 sleepy or unreachable.
    """
    body = _probe_printer(timeout=READY_TIMEOUT_S)
    if body.get("ok"):
        return body
    return JSONResponse(status_code=503, content=body)


@app.get("/status")
def status():
    """Same probe as /ready, always HTTP 200 — friendlier for HA REST sensors."""
    return _probe_printer(timeout=READY_TIMEOUT_S)


@app.post("/printer/wake")
def printer_wake():
    """
    Bounded nudge: optional bluetoothctl disconnect/connect, then RFCOMM probe.
    Does not loop — HA owns attempt limits / cooldown.
    """
    cfg = get_config()
    mac = cfg["mac"]
    log.info("event=wake_start mac=%s", mac)
    bt_note = _bluetoothctl_nudge(mac)
    body = _probe_printer(timeout=max(READY_TIMEOUT_S, 8.0))
    if bt_note:
        body = {**body, "bluetoothctl": bt_note}
    if body.get("ok"):
        log.info("event=wake_ok mac=%s printer=%s", mac, body.get("printer"))
        return body
    log.warning("event=wake_fail mac=%s detail=%s", mac, body.get("detail"))
    return JSONResponse(status_code=503, content=body)


def _print_with_session(job: str, req_id: str, fn):
    with _print_lock:
        try:
            with printer_session() as soc:
                fn(soc)
        except OSError as e:
            log.error("event=print_fail job=%s req_id=%s error=%s", job, req_id, e)
            raise HTTPException(status_code=502, detail=f"Printer connection failed: {e}") from e
        except Exception as e:
            log.error("event=print_fail job=%s req_id=%s error=%s", job, req_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
    log.info("event=print_ok job=%s req_id=%s", job, req_id)


@app.post("/print/text")
def print_text_endpoint(request: Request, body: TextPrintRequest):
    req_id = getattr(request.state, "req_id", "-")
    log.info(
        "event=print_start job=text req_id=%s chars=%s font_size=%s",
        req_id,
        len(body.text),
        body.font_size,
    )
    _print_with_session(
        "text",
        req_id,
        lambda soc: print_text(soc, body.text, font_size=body.font_size),
    )
    return {"ok": True, "printed": "text"}


@app.post("/print/markdown")
def print_markdown_endpoint(request: Request, body: MarkdownPrintRequest):
    req_id = getattr(request.state, "req_id", "-")
    cfg = get_config()
    log.info(
        "event=print_start job=markdown req_id=%s chars=%s",
        req_id,
        len(body.markdown),
    )
    try:
        img = render_markdown(
            body.markdown,
            width=cfg["width"],
            font_path=cfg["font_path"],
        )
    except Exception as e:
        log.error("event=render_fail job=markdown req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=500, detail=f"Markdown render failed: {e}") from e

    _print_with_session("markdown", req_id, lambda soc: print_image(soc, img))
    return {"ok": True, "printed": "markdown"}


@app.post("/print/image")
def print_image_endpoint(request: Request, file: UploadFile = File(...)):
    req_id = getattr(request.state, "req_id", "-")
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large (max {MAX_UPLOAD_BYTES} bytes)",
        )

    try:
        img = PIL.Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}") from e

    w, h = img.size
    if w * h > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large ({w}x{h}; max {MAX_IMAGE_PIXELS} pixels)",
        )

    cfg = get_config()
    log.info(
        "event=print_start job=image req_id=%s filename=%s bytes=%s size=%sx%s",
        req_id,
        file.filename,
        len(raw),
        w,
        h,
    )
    prepared = prepare_raster_image(img, cfg["width"]).convert("1")
    _print_with_session("image", req_id, lambda soc: print_image(soc, prepared))
    return {"ok": True, "printed": "image", "filename": file.filename}


@app.post("/print/reddit")
def print_reddit_endpoint(
    request: Request,
    body: RedditPrintRequest | None = None,
):
    """Random printable image from a subreddit (DEFAULT_SUBREDDIT if omitted)."""
    req_id = getattr(request.state, "req_id", "-")
    sub = (body.subreddit if body and body.subreddit else None) or _default_subreddit()
    cfg = get_config()
    log.info("event=print_start job=reddit req_id=%s subreddit=%s", req_id, sub)

    try:
        img, post = fetch_random_subreddit_image(sub)
    except RedditImageError as e:
        log.error("event=fetch_fail job=reddit req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        log.error("event=fetch_fail job=reddit req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=500, detail=f"Reddit fetch failed: {e}") from e

    prepared = prepare_raster_image(img, cfg["width"]).convert("1")
    _print_with_session("reddit", req_id, lambda soc: print_image(soc, prepared))

    title = post.get("title", "")
    url = post.get("url", "")
    log.info(
        "event=reddit_picked req_id=%s subreddit=%s title=%r url=%s",
        req_id,
        sub,
        title[:80],
        url,
    )
    return {
        "ok": True,
        "printed": "reddit",
        "subreddit": sub.lstrip("r/").strip("/"),
        "title": title,
        "url": url,
    }


def main():
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8080"))
    uvicorn.run("api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
