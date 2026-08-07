"""Stdout + optional rotating file log (Samba-visible under /share in the add-on)."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
DEFAULT_ADDON_LOG = "/share/cat_printer/addon.log"


def configure_logging(
    *,
    log_file: str | None = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> str | None:
    """Configure root logging. Returns the file path used, or None if file skipped."""
    fmt = logging.Formatter(LOG_FORMAT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not _has_stream_handler(root):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)

    path = (log_file if log_file is not None else os.environ.get("LOG_FILE", "")).strip()
    if not path:
        return None

    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        logging.getLogger("cat_printer.log").warning(
            "event=log_file_skip path=%s error=%s", path, e
        )
        return None

    return path


def _has_stream_handler(logger: logging.Logger) -> bool:
    for handler in logger.handlers:
        if type(handler) is logging.StreamHandler:
            return True
    return False
