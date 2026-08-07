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


def test_estimate_print_height_scales_wide_images():
    import PIL.Image
    from yhk_printer import estimate_print_height

    wide = PIL.Image.new("1", (768, 200), 1)
    assert estimate_print_height(wide, width=384) == 100
    fitted = PIL.Image.new("1", (384, 200), 1)
    assert estimate_print_height(fitted, width=384) == 200
