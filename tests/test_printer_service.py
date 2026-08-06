"""printer_service domain helpers (no Bluetooth)."""
import pytest

from printer_service import PrintFailed, PrinterError, PrinterUnavailable


def test_domain_errors_are_distinct():
    assert issubclass(PrinterUnavailable, PrinterError)
    assert issubclass(PrintFailed, PrinterError)
    with pytest.raises(PrinterUnavailable):
        raise PrinterUnavailable("sleepy")
