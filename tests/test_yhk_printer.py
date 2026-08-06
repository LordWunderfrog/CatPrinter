"""Unit tests for connect retry helpers (no Bluetooth)."""
import errno

from yhk_printer import is_retryable_connect_error


def test_retryable_host_is_down():
    assert is_retryable_connect_error(OSError(errno.EHOSTDOWN, "Host is down"))
    assert is_retryable_connect_error(OSError(errno.ETIMEDOUT, "timed out"))
    assert not is_retryable_connect_error(OSError(errno.EPERM, "Operation not permitted"))
    assert not is_retryable_connect_error(ValueError("nope"))
