"""
HTTP API for the YHK cat printer. Intended for LAN / HA / reverse-proxy callers.

  GET  /health   — process up (no auth, no BT)
  GET  /ready    — RFCOMM probe; 503 if sleepy/unreachable (no auth)
  GET  /status   — same probe as /ready but always HTTP 200 (HA sensors)
  POST /printer/wake — BT nudge + RFCOMM probe (auth if API_TOKEN set)
  POST /print/text|markdown|image|reddit — validate, spool to disk, 202

Env (in addition to yhk_printer / printer_service):
  API_HOST, API_PORT, API_TOKEN, DEFAULT_SUBREDDIT
  MAX_TEXT_CHARS, MAX_MARKDOWN_CHARS, MAX_UPLOAD_BYTES, MAX_IMAGE_PIXELS
  MAX_RENDER_HEIGHT, MAX_PRINT_QUEUE, PRINT_SPOOL_DIR, SPOOL_TTL_S

Print routes write a raster to the disk spool and return 202. Drain runs
opportunistically (after enqueue if awake, after wake/status when awake) —
no forever RFCOMM polling. Callers stay dumb: accepted or rejected.

Auth (only if API_TOKEN is set): X-Api-Key or Authorization: Bearer.
NFC / HA: same on /print/* and /printer/wake. /health, /ready, /status stay open.
"""
from __future__ import annotations

import io
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import PIL.Image
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from image_prep import prepare_raster_image
from markdown_renderer import RenderTooTall, render_markdown
import printer_service
from print_spool import QueueFull, print_spool
from reddit_image import RedditImageError, fetch_random_subreddit_image
from yhk_printer import create_text_image, get_config

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
# ~2.5m of paper at ~8 dots/mm — long recipes OK; newline bombs fail hard.
MAX_RENDER_HEIGHT = int(os.environ.get("MAX_RENDER_HEIGHT", str(20_000)))
READY_TIMEOUT_S = float(os.environ.get("READY_TIMEOUT_S", "5"))


def _api_token() -> str:
    return (os.environ.get("API_TOKEN") or "").strip()


def _default_subreddit() -> str:
    return (os.environ.get("DEFAULT_SUBREDDIT") or "wunkus").strip() or "wunkus"


class TextPrintRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    font_size: int = Field(65, ge=8, le=200)


class MarkdownPrintRequest(BaseModel):
    markdown: str = Field(..., min_length=1, max_length=MAX_MARKDOWN_CHARS)


class RedditPrintRequest(BaseModel):
    subreddit: str | None = Field(None, min_length=1, max_length=64)


def _compose_captioned_image(
    caption: str,
    photo: PIL.Image.Image,
    *,
    width: int,
    font_path: str,
    font_size: int = 22,
) -> PIL.Image.Image:
    """Title (wrapped text) above a dithered photo, single 1-bit strip."""
    photo_1 = prepare_raster_image(photo, width).convert("1")
    text = (caption or "").strip()
    if not text:
        return photo_1
    title_img = create_text_image(
        text, width, font_path=font_path, font_size=font_size
    ).convert("1")
    gap = 8
    out = PIL.Image.new("1", (width, title_img.height + gap + photo_1.height), 1)
    out.paste(title_img, (0, 0))
    out.paste(photo_1, (0, title_img.height + gap))
    return out


def _maybe_drain_after_probe(body: dict, *, reason: str) -> None:
    if body.get("printer") in ("awake", "busy"):
        print_spool.drain_async(reason=reason)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_config()
    token_on = bool(_api_token())
    log.info(
        "event=startup printer_mac=%s printer_port=%s auth=%s default_subreddit=%s "
        "spool=%s queue_max=%s",
        cfg["mac"],
        cfg["port"],
        "on" if token_on else "off",
        _default_subreddit(),
        print_spool.stats()["spool_dir"],
        print_spool.maxsize,
    )
    # Rebuild/restart: try once if the cat is already up.
    print_spool.drain_async(reason="startup")
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


def _path_requires_auth(path: str) -> bool:
    return path.startswith("/print") or path == "/printer/wake"


def _enqueue_or_http(
    kind: str,
    req_id: str,
    img: PIL.Image.Image,
    **extra,
) -> JSONResponse:
    """Spool a prepared raster. Printer offline is not a reject."""
    try:
        job_id = print_spool.submit(kind=kind, req_id=req_id, image=img, meta=extra)
    except QueueFull as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    body = {
        "ok": True,
        "queued": True,
        "job_id": job_id,
        "printed": kind,
        **extra,
    }
    return JSONResponse(status_code=202, content=body)


@app.middleware("http")
async def auth_and_request_log(request: Request, call_next):
    req_id = uuid.uuid4().hex[:8]
    request.state.req_id = req_id
    started = time.perf_counter()

    path = request.url.path
    if _path_requires_auth(path):
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
        **print_spool.stats(),
    }


@app.get("/ready")
def ready():
    """
    Readiness: can we open RFCOMM to the printer right now?
    200 awake / busy; 503 sleepy or unreachable.
    """
    body = printer_service.probe(timeout=READY_TIMEOUT_S)
    _maybe_drain_after_probe(body, reason="ready")
    if body.get("ok"):
        return body
    return JSONResponse(status_code=503, content=body)


@app.get("/status")
def status():
    """Same probe as /ready, always HTTP 200 — friendlier for HA REST sensors."""
    body = printer_service.probe(timeout=READY_TIMEOUT_S)
    _maybe_drain_after_probe(body, reason="status")
    return body


@app.post("/printer/wake")
def printer_wake():
    """
    Bounded nudge: optional bluetoothctl disconnect/connect, then RFCOMM probe.
    Does not loop — HA owns attempt limits / cooldown.
    """
    body = printer_service.wake(probe_timeout=max(READY_TIMEOUT_S, 8.0))
    _maybe_drain_after_probe(body, reason="wake")
    if body.get("ok"):
        return body
    return JSONResponse(status_code=503, content=body)


@app.post("/print/text")
def print_text_endpoint(request: Request, body: TextPrintRequest):
    req_id = getattr(request.state, "req_id", "-")
    cfg = get_config()
    log.info(
        "event=print_start job=text req_id=%s chars=%s font_size=%s",
        req_id,
        len(body.text),
        body.font_size,
    )
    try:
        img = create_text_image(
            body.text,
            cfg["width"],
            font_path=cfg["font_path"],
            font_size=body.font_size,
            max_height=MAX_RENDER_HEIGHT,
        ).convert("1")
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    return _enqueue_or_http("text", req_id, img)


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
            allow_local_images=False,
            max_height=MAX_RENDER_HEIGHT,
        )
    except RenderTooTall as e:
        log.warning("event=render_too_tall job=markdown req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        log.error("event=render_fail job=markdown req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=500, detail=f"Markdown render failed: {e}") from e

    return _enqueue_or_http("markdown", req_id, img)


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
    return _enqueue_or_http("image", req_id, prepared, filename=file.filename)


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
        img, post = fetch_random_subreddit_image(
            sub,
            max_bytes=MAX_UPLOAD_BYTES,
            max_pixels=MAX_IMAGE_PIXELS,
        )
    except RedditImageError as e:
        log.error("event=fetch_fail job=reddit req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        log.error("event=fetch_fail job=reddit req_id=%s error=%s", req_id, e)
        raise HTTPException(status_code=500, detail=f"Reddit fetch failed: {e}") from e

    prepared = _compose_captioned_image(
        post.get("title") or "",
        img,
        width=cfg["width"],
        font_path=cfg["font_path"],
    )
    if prepared.height > MAX_RENDER_HEIGHT:
        raise HTTPException(
            status_code=413,
            detail=(
                f"reddit render too tall ({prepared.height}px; "
                f"max {MAX_RENDER_HEIGHT}px)"
            ),
        )
    title = post.get("title", "")
    url = post.get("url", "")
    log.info(
        "event=reddit_picked req_id=%s subreddit=%s title=%r url=%s",
        req_id,
        sub,
        title[:80],
        url,
    )
    return _enqueue_or_http(
        "reddit",
        req_id,
        prepared,
        subreddit=sub.lstrip("r/").strip("/"),
        title=title,
        url=url,
    )


def main():
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8080"))
    uvicorn.run("api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
