"""printer_service domain helpers (no Bluetooth)."""
import threading
import time

import pytest

from printer_service import (
    PrintFailed,
    PrinterError,
    PrinterUnavailable,
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
    monkeypatch.setattr(
        "printer_service._run_session_settled", lambda *a, **k: None
    )
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
