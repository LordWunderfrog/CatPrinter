"""log_setup: optional rotating file handler."""
from __future__ import annotations

import logging
from pathlib import Path

from log_setup import configure_logging


def _reset_root_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_configure_logging_writes_file(tmp_path: Path, monkeypatch):
    _reset_root_handlers()
    monkeypatch.delenv("LOG_FILE", raising=False)
    path = tmp_path / "cat_printer" / "addon.log"
    used = configure_logging(log_file=str(path))
    assert used == str(path)
    assert path.exists()
    logging.getLogger("cat_printer.test").info("event=log_setup_ok")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "event=log_setup_ok" in text
    assert text.count("event=log_setup_ok") == 1
    _reset_root_handlers()


def test_configure_logging_idempotent(tmp_path: Path, monkeypatch):
    _reset_root_handlers()
    monkeypatch.delenv("LOG_FILE", raising=False)
    path = tmp_path / "addon.log"
    assert configure_logging(log_file=str(path)) == str(path)
    assert configure_logging(log_file=str(path)) == str(path)
    file_handlers = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "cat_printer_log", None) == "file"
    ]
    assert len(file_handlers) == 1
    logging.getLogger("cat_printer.test").info("event=once")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert path.read_text(encoding="utf-8").count("event=once") == 1
    _reset_root_handlers()


def test_configure_logging_skips_empty(monkeypatch):
    _reset_root_handlers()
    monkeypatch.setenv("LOG_FILE", "")
    assert configure_logging() is None
    _reset_root_handlers()
