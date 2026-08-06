"""Unit tests for connect retry helpers (no Bluetooth)."""
import errno

from yhk_printer import _is_retryable_connect_error


def test_retryable_host_is_down():
    assert _is_retryable_connect_error(OSError(errno.EHOSTDOWN, "Host is down"))
    assert _is_retryable_connect_error(OSError(errno.ETIMEDOUT, "timed out"))
    assert not _is_retryable_connect_error(OSError(errno.EPERM, "Operation not permitted"))
    assert not _is_retryable_connect_error(ValueError("nope"))
