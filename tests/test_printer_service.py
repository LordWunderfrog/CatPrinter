"""printer_service domain helpers (no Bluetooth)."""
import errno
import threading
import time

import pytest

from printer_service import (
    PrintFailed,
    PrinterError,
    PrinterUnavailable,
    hold_printer,
    probe,
    run_print,
)


def test_domain_errors_are_distinct():
    assert issubclass(PrinterUnavailable, PrinterError)
    assert issubclass(PrintFailed, PrinterError)
    with pytest.raises(PrinterUnavailable):
        raise PrinterUnavailable("sleepy")


def test_run_print_holds_lock_during_settle(monkeypatch):
    """Status must not RFCOMM while mechanical settle still owns the lock."""

    def fake_settled(_job, _req_id, fn):
        fn(object())

    monkeypatch.setattr("printer_service._run_session_settled", fake_settled)
    saw_busy = threading.Event()

    def other():
        time.sleep(0.05)
        body = probe(timeout=0.1)
        if body.get("printer") == "busy":
            saw_busy.set()

    t = threading.Thread(target=other, daemon=True)
    t.start()
    run_print("test", "req", lambda soc: None, settle_s=0.25)
    t.join(timeout=2)
    assert saw_busy.is_set()


def test_hold_printer_blocks_probe_between_jobs():
    """Drain-wide hold must keep status from opening RFCOMM between jobs."""
    saw_busy = threading.Event()

    def other():
        time.sleep(0.05)
        if probe(timeout=0.1).get("printer") == "busy":
            saw_busy.set()

    t = threading.Thread(target=other, daemon=True)
    t.start()
    with hold_printer():
        time.sleep(0.2)
    t.join(timeout=2)
    assert saw_busy.is_set()


def test_no_wake_retry_after_partial_send(monkeypatch):
    """Connection drop mid-send must not bluetoothctl-retry and reprint."""
    nudged: list[int] = []
    monkeypatch.setattr(
        "printer_service.bluetoothctl_nudge",
        lambda *a, **k: nudged.append(1),
    )

    def boom(_soc):
        raise OSError(errno.ECONNRESET, "Connection reset")

    def fake_session(fn):
        fn(object())

    monkeypatch.setattr("printer_service._run_session", fake_session)
    with pytest.raises(PrintFailed):
        run_print("reddit", "abc", boom, settle_s=0)
    assert nudged == []
