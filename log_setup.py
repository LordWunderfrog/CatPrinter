"""Stdout + rotating file logs (Samba-visible under /share in the add-on)."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Glanceable: no milliseconds, fixed level width.
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_ADDON_LOG = "/share/cat_printer/addon.log"
DEFAULT_PROBE_LOG = "/share/cat_printer/probe.log"
_HANDLER_MARK = "cat_printer_log"
_PROBE_HANDLER_MARK = "cat_printer_probe_log"


def configure_logging(
    *,
    log_file: str | None = None,
    probe_log_file: str | None = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> tuple[str | None, str | None]:
    """
    Configure root + probe loggers once.

    Returns (addon_log_path, probe_log_path). Idempotent — will not stack handlers.
    Probe logger does not propagate to root (keeps main log job-centric).
    """
    fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    _ensure_stream_handler(root, fmt)

    path = (log_file if log_file is not None else os.environ.get("LOG_FILE", "")).strip()
    if path:
        path = _ensure_marked_file_handler(
            root, fmt, path, max_bytes, backup_count, mark=_HANDLER_MARK
        )

    probe_path = (
        probe_log_file
        if probe_log_file is not None
        else os.environ.get("PROBE_LOG_FILE", "")
    ).strip()
    # Default probe log beside addon.log when LOG_FILE is under /share/...
    if not probe_path and path:
        probe_path = str(Path(path).with_name("probe.log"))
    if not probe_path:
        probe_path = os.environ.get("PROBE_LOG_FILE", DEFAULT_PROBE_LOG).strip()

    probe_logger = logging.getLogger("cat_printer.probe")
    probe_logger.setLevel(logging.INFO)
    probe_logger.propagate = False
    if probe_path:
        used = _ensure_marked_file_handler(
            probe_logger,
            fmt,
            probe_path,
            max_bytes,
            backup_count,
            mark=_PROBE_HANDLER_MARK,
        )
        probe_path = used
    else:
        probe_path = None

    # Uvicorn / asyncio chatter does not belong next to print jobs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return (path or None), (probe_path or None)


def _ensure_stream_handler(root: logging.Logger, fmt: logging.Formatter) -> None:
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARK, None) == "stream":
            handler.setFormatter(fmt)
            return
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    setattr(stream, _HANDLER_MARK, "stream")
    root.addHandler(stream)


def _ensure_marked_file_handler(
    logger: logging.Logger,
    fmt: logging.Formatter,
    path: str,
    max_bytes: int,
    backup_count: int,
    *,
    mark: str,
) -> str | None:
    for handler in logger.handlers:
        if getattr(handler, mark, None) == "file":
            if getattr(handler, "baseFilename", None) == str(Path(path).resolve()):
                handler.setFormatter(fmt)
                return path
            logger.removeHandler(handler)
            handler.close()

    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        setattr(file_handler, mark, "file")
        logger.addHandler(file_handler)
        return path
    except OSError as e:
        logging.getLogger("cat_printer.log").warning(
            "event=log_file_skip path=%s error=%s", path, e
        )
        return None
