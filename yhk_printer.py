"""
YHK cat/rabbit thermal printer (Classic Bluetooth RFCOMM).
Reusable library: connect, print_image, print_text.
Config from env: PRINTER_MAC, PRINTER_PORT, PRINTER_WIDTH, PRINTER_FONT,
  PRINTER_CONNECT_RETRIES, PRINTER_CONNECT_RETRY_DELAY.
"""
import errno
import os
import socket
import struct
from contextlib import contextmanager
from time import sleep

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import PIL.ImageChops
import PIL.ImageOps

# Transient Classic-BT failures (sleepy / radio glitch). Not a keep-awake strategy.
_RETRYABLE_ERRNOS = {
    errno.EHOSTDOWN,  # 112 Host is down (common on HAOS after drop)
    errno.EHOSTUNREACH,
    errno.ETIMEDOUT,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
}


def get_config():
    """Read printer config from environment with defaults."""
    return {
        "mac": os.environ.get("PRINTER_MAC", "25:00:27:00:1B:D5"),
        "port": int(os.environ.get("PRINTER_PORT", "2")),
        "width": int(os.environ.get("PRINTER_WIDTH", "384")),
        "font_path": os.environ.get("PRINTER_FONT", "Lucon.ttf"),
        "connect_retries": int(os.environ.get("PRINTER_CONNECT_RETRIES", "3")),
        "connect_retry_delay": float(os.environ.get("PRINTER_CONNECT_RETRY_DELAY", "1.5")),
    }


def is_retryable_connect_error(exc: BaseException) -> bool:
    if not isinstance(exc, OSError):
        return False
    if exc.errno in _RETRYABLE_ERRNOS:
        return True
    msg = str(exc).lower()
    return "host is down" in msg or "timed out" in msg


def connect(mac=None, port=None, timeout=10.0, retries=None, retry_delay=None):
    """
    Open RFCOMM socket to the printer. Retries transient Host-is-down / timeout.
    If mac/port are None, use get_config().
    """
    cfg = get_config()
    mac = mac or cfg["mac"]
    port = port if port is not None else cfg["port"]
    attempts = max(1, retries if retries is not None else cfg["connect_retries"])
    delay = retry_delay if retry_delay is not None else cfg["connect_retry_delay"]

    last_err: BaseException | None = None
    for attempt in range(1, attempts + 1):
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(timeout)
        try:
            s.connect((mac, port))
            return s
        except OSError as e:
            last_err = e
            try:
                s.close()
            except OSError:
                pass
            if attempt >= attempts or not is_retryable_connect_error(e):
                raise
            sleep(delay)
    assert last_err is not None
    raise last_err


@contextmanager
def printer_session(probe=True, timeout=10.0):
    """
    Open a printer connection, optionally probe status/serial/product, then close.
    Use for one-shot print jobs (CLI, API).
    """
    s = connect(timeout=timeout)
    try:
        if probe:
            get_printer_status(s)
            sleep(0.5)
            get_printer_serial_number(s)
            sleep(0.5)
            get_printer_product_info(s)
            sleep(0.5)
        yield s
    finally:
        s.close()


def _initialize_printer(soc):
    soc.send(b"\x1b\x40")


def get_printer_status(soc):
    soc.send(b"\x1e\x47\x03")
    return soc.recv(38)


def get_printer_serial_number(soc):
    soc.send(b"\x1D\x67\x39")
    return soc.recv(21)


def get_printer_product_info(soc):
    soc.send(b"\x1d\x67\x69")
    return soc.recv(16)


def _send_start_print_sequence(soc):
    soc.send(b"\x1d\x49\xf0\x19")


def _send_end_print_sequence(soc):
    soc.send(b"\x0a\x0a\x0a\x0a")


def _trim_image(im):
    bg = PIL.Image.new(im.mode, im.size, (255, 255, 255))
    diff = PIL.ImageChops.difference(im, bg)
    diff = PIL.ImageChops.add(diff, diff, 2.0)
    bbox = diff.getbbox()
    if bbox:
        return im.crop((bbox[0], bbox[1], bbox[2], bbox[3] + 10))
    return im


def _get_wrapped_text(text: str, font: PIL.ImageFont.ImageFont, line_length: int):
    lines = [""]
    for word in text.split():
        line = f"{lines[-1]} {word}".strip()
        if font.getlength(line) <= line_length:
            lines[-1] = line
        else:
            lines.append(word)
    return "\n".join(lines)


def create_text_image(text, width, font_path="Lucon.ttf", font_size=12, max_height=None):
    """Render text to a PIL Image sized for the printer (trimmed to content)."""
    font = PIL.ImageFont.truetype(font_path, font_size)
    # Measure first so long jobs aren't silently clipped by a fixed canvas.
    probe = PIL.Image.new("RGB", (width, 10), color=(255, 255, 255))
    probe_draw = PIL.ImageDraw.Draw(probe)
    lines = []
    for line in text.splitlines() or [""]:
        lines.append(_get_wrapped_text(line, font, width))
    body = "\n".join(lines)
    bbox = probe_draw.multiline_textbbox((0, 0), body, font=font)
    height = max(bbox[3] - bbox[1] + 20, font_size + 20)
    if max_height is not None and height > max_height:
        raise ValueError(
            f"Text render height {height}px exceeds max {max_height}px"
        )
    img = PIL.Image.new("RGB", (width, height), color=(255, 255, 255))
    d = PIL.ImageDraw.Draw(img)
    d.text((0, 0), body, fill=(0, 0, 0), font=font)
    return _trim_image(img)


def print_image(soc, im, width=None):
    """
    Send a PIL Image to the printer over the open socket.
    If width is None, use get_config()['width'].
    Pre-dithered mode "1" images are sent without a second dither pass.
    """
    cfg = get_config()
    width = width if width is not None else cfg["width"]

    if im.width > width:
        height = max(1, int(im.height * (width / im.width)))
        im = im.resize((width, height), PIL.Image.Resampling.NEAREST if im.mode == "1" else PIL.Image.Resampling.LANCZOS)

    if im.width < width:
        padded_image = PIL.Image.new("1", (width, im.height), 1)
        padded_image.paste(im.convert("1") if im.mode != "1" else im)
        im = padded_image

    im = im.rotate(180)

    if im.mode != "1":
        im = im.convert("1", dither=PIL.Image.Dither.FLOYDSTEINBERG)

    if im.size[0] % 8:
        im2 = PIL.Image.new(
            "1", (im.size[0] + 8 - im.size[0] % 8, im.size[1]), "white"
        )
        im2.paste(im, (0, 0))
        im = im2

    # Invert for this printer; keep binary (no dither) so pre-processed photos stay intact.
    im = PIL.ImageOps.invert(im.convert("L")).convert(
        "1", dither=PIL.Image.Dither.NONE
    )

    buf = b"".join(
        (
            bytearray(b"\x1d\x76\x30\x00"),
            struct.pack("2B", int(im.size[0] / 8 % 256), int(im.size[0] / 8 / 256)),
            struct.pack("2B", int(im.size[1] % 256), int(im.size[1] / 256)),
            im.tobytes(),
        )
    )
    _initialize_printer(soc)
    sleep(0.5)
    _send_start_print_sequence(soc)
    sleep(0.5)
    soc.sendall(buf)
    sleep(0.5)
    _send_end_print_sequence(soc)
    sleep(0.5)


def print_text(soc, text, font_path=None, width=None, font_size=12):
    """
    Render text to an image and print it. If font_path/width are None, use get_config().
    """
    cfg = get_config()
    width = width if width is not None else cfg["width"]
    font_path = font_path or cfg["font_path"]
    im = create_text_image(text, width, font_path, font_size)
    print_image(soc, im, width)
