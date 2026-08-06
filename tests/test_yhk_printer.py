"""Unit tests for connect retry helpers (no Bluetooth)."""
import errno

from yhk_printer import is_retryable_connect_error


def test_retryable_host_is_down():
    assert is_retryable_connect_error(OSError(errno.EHOSTDOWN, "Host is down"))
    assert is_retryable_connect_error(OSError(errno.ETIMEDOUT, "timed out"))
    assert not is_retryable_connect_error(OSError(errno.EPERM, "Operation not permitted"))
    assert not is_retryable_connect_error(ValueError("nope"))


def test_busy_error():
    from yhk_printer import is_busy_error

    assert is_busy_error(OSError(errno.EBUSY, "Device or resource busy"))
    assert is_busy_error(OSError(16, "Device or resource busy"))
    assert not is_busy_error(OSError(errno.EHOSTDOWN, "Host is down"))
