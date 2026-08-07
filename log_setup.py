"""Stdout + optional rotating file log (Samba-visible under /share in the add-on)."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Glanceable: no milliseconds, fixed level width.
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_ADDON_LOG = "/share/cat_printer/addon.log"
_HANDLER_MARK = "cat_printer_log"


def configure_logging(
    *,
    log_file: str | None = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> str | None:
    """
    Configure root logging once.

    Idempotent: re-import / double configure_logging() will not stack handlers
    (that was doubling every line in addon.log).
    """
    fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    _ensure_stream_handler(root, fmt)

    path = (log_file if log_file is not None else os.environ.get("LOG_FILE", "")).strip()
    if path:
        path = _ensure_file_handler(root, fmt, path, max_bytes, backup_count)

    # Uvicorn / asyncio chatter does not belong next to print jobs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return path or None


def _ensure_stream_handler(root: logging.Logger, fmt: logging.Formatter) -> None:
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARK, None) == "stream":
            handler.setFormatter(fmt)
            return
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    setattr(stream, _HANDLER_MARK, "stream")
    root.addHandler(stream)


def _ensure_file_handler(
    root: logging.Logger,
    fmt: logging.Formatter,
    path: str,
    max_bytes: int,
    backup_count: int,
) -> str | None:
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARK, None) == "file":
            # Same path already wired — do not add another RotatingFileHandler.
            if getattr(handler, "baseFilename", None) == str(Path(path).resolve()):
                handler.setFormatter(fmt)
                return path
            # Different path (tests): replace.
            root.removeHandler(handler)
            handler.close()

    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        resolved = str(Path(path).resolve())
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        setattr(file_handler, _HANDLER_MARK, "file")
        root.addHandler(file_handler)
        return path
    except OSError as e:
        logging.getLogger("cat_printer.log").warning(
            "event=log_file_skip path=%s error=%s", path, e
        )
        return None
