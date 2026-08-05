"""
HTTP API for the YHK cat printer. Intended for LAN / HA / reverse-proxy callers.

  GET  /health
  POST /print/text   JSON: {"text": "...", "font_size": 65}
  POST /print/image  multipart form field "file" (image)

Env (in addition to yhk_printer): API_HOST, API_PORT.
"""
import io
import threading
from contextlib import asynccontextmanager

import PIL.Image
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from yhk_printer import get_config, print_image, print_text, printer_session

_print_lock = threading.Lock()


class TextPrintRequest(BaseModel):
    text: str = Field(..., min_length=1)
    font_size: int = Field(65, ge=8, le=200)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_config()
    print(f"Print API ready (printer {cfg['mac']}:{cfg['port']})")
    yield


app = FastAPI(title="Cat Printer", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    cfg = get_config()
    return {"ok": True, "printer_mac": cfg["mac"], "printer_port": cfg["port"]}


@app.post("/print/text")
def print_text_endpoint(body: TextPrintRequest):
    with _print_lock:
        try:
            with printer_session() as soc:
                print_text(soc, body.text, font_size=body.font_size)
        except OSError as e:
            raise HTTPException(status_code=502, detail=f"Printer connection failed: {e}") from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "printed": "text"}


@app.post("/print/image")
async def print_image_endpoint(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        img = PIL.Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}") from e

    with _print_lock:
        try:
            with printer_session() as soc:
                print_image(soc, img)
        except OSError as e:
            raise HTTPException(status_code=502, detail=f"Printer connection failed: {e}") from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "printed": "image", "filename": file.filename}


def main():
    import os

    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8080"))
    uvicorn.run("api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
