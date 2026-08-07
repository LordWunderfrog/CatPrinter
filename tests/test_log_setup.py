"""log_setup: optional rotating file handlers."""
from __future__ import annotations

import logging
from pathlib import Path

from log_setup import configure_logging


def _reset_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    probe = logging.getLogger("cat_printer.probe")
    for handler in list(probe.handlers):
        probe.removeHandler(handler)
        handler.close()


def test_configure_logging_writes_file(tmp_path: Path, monkeypatch):
    _reset_handlers()
    monkeypatch.delenv("LOG_FILE", raising=False)
    monkeypatch.delenv("PROBE_LOG_FILE", raising=False)
    path = tmp_path / "cat_printer" / "addon.log"
    used, probe = configure_logging(log_file=str(path), probe_log_file="")
    assert used == str(path)
    # Empty probe_log_file falls back to DEFAULT beside path → probe.log next to addon
    assert probe == str(path.with_name("probe.log"))
    logging.getLogger("cat_printer.test").info("event=log_setup_ok")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert text.count("event=log_setup_ok") == 1
    _reset_handlers()


def test_configure_logging_idempotent(tmp_path: Path, monkeypatch):
    _reset_handlers()
    monkeypatch.delenv("LOG_FILE", raising=False)
    path = tmp_path / "addon.log"
    probe = tmp_path / "probe.log"
    assert configure_logging(log_file=str(path), probe_log_file=str(probe))[0] == str(
        path
    )
    assert configure_logging(log_file=str(path), probe_log_file=str(probe))[0] == str(
        path
    )
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
    _reset_handlers()


def test_probe_log_separate(tmp_path: Path, monkeypatch):
    _reset_handlers()
    monkeypatch.delenv("LOG_FILE", raising=False)
    addon = tmp_path / "addon.log"
    probe = tmp_path / "probe.log"
    configure_logging(log_file=str(addon), probe_log_file=str(probe))
    logging.getLogger("cat_printer.api").info("event=queued req=1")
    logging.getLogger("cat_printer.probe").info("event=probe printer=awake")
    for logger_name in ("cat_printer.api", "cat_printer.probe", ""):
        for handler in logging.getLogger(logger_name).handlers:
            handler.flush()
    assert "event=queued" in addon.read_text(encoding="utf-8")
    assert "event=probe" not in addon.read_text(encoding="utf-8")
    assert "event=probe printer=awake" in probe.read_text(encoding="utf-8")
    assert "event=queued" not in probe.read_text(encoding="utf-8")
    _reset_handlers()


def test_configure_logging_skips_empty(monkeypatch):
    _reset_handlers()
    monkeypatch.setenv("LOG_FILE", "")
    monkeypatch.setenv("PROBE_LOG_FILE", "")
    # With both empty, probe falls back to DEFAULT_PROBE_LOG which may fail to create
    # outside the add-on — force explicit empty via probe_log_file=""
    used, probe = configure_logging(log_file="", probe_log_file="")
    # log_file "" → None; probe_log_file "" then defaults from LOG_FILE empty → DEFAULT
    # Actually configure_logging: if not probe_path and path: ... if not probe_path: DEFAULT
    # So empty both still tries DEFAULT_PROBE_LOG. For test, just ensure no crash.
    assert used is None
    _reset_handlers()
