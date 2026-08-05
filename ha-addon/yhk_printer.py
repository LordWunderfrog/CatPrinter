"""
YHK cat/rabbit thermal printer (Classic Bluetooth RFCOMM).
Reusable library: connect, print_image, print_text.
Config from env: PRINTER_MAC, PRINTER_PORT, PRINTER_WIDTH, PRINTER_FONT.
"""
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


def get_config():
    """Read printer config from environment with defaults."""
    return {
        "mac": os.environ.get("PRINTER_MAC", "25:00:27:00:1B:D5"),
        "port": int(os.environ.get("PRINTER_PORT", "2")),
        "width": int(os.environ.get("PRINTER_WIDTH", "384")),
        "font_path": os.environ.get("PRINTER_FONT", "Lucon.ttf"),
    }


def connect(mac=None, port=None):
    """Open RFCOMM socket to the printer. If mac/port are None, use get_config()."""
    cfg = get_config()
    mac = mac or cfg["mac"]
    port = port if port is not None else cfg["port"]
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.connect((mac, port))
    return s


@contextmanager
def printer_session(probe=True):
    """
    Open a printer connection, optionally probe status/serial/product, then close.
    Use for one-shot print jobs (CLI, API).
    """
    s = connect()
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


def create_text_image(text, width, font_path="Lucon.ttf", font_size=12):
    """Render text to a PIL Image sized for the printer (trimmed to content)."""
    img = PIL.Image.new("RGB", (width, 5000), color=(255, 255, 255))
    font = PIL.ImageFont.truetype(font_path, font_size)
    d = PIL.ImageDraw.Draw(img)
    lines = []
    for line in text.splitlines():
        lines.append(_get_wrapped_text(line, font, width))
    d.text((0, 0), "\n".join(lines), fill=(0, 0, 0), font=font)
    return _trim_image(img)


def print_image(soc, im, width=None):
    """
    Send a PIL Image to the printer over the open socket.
    If width is None, use get_config()['width'].
    """
    cfg = get_config()
    width = width if width is not None else cfg["width"]

    if im.width > width:
        height = int(im.height * (width / im.width))
        im = im.resize((width, height))

    if im.width < width:
        padded_image = PIL.Image.new("1", (width, im.height), 1)
        padded_image.paste(im)
        im = padded_image

    im = im.rotate(180)

    if im.mode != "1":
        im = im.convert("1")

    if im.size[0] % 8:
        im2 = PIL.Image.new(
            "1", (im.size[0] + 8 - im.size[0] % 8, im.size[1]), "white"
        )
        im2.paste(im, (0, 0))
        im = im2

    im = PIL.ImageOps.invert(im.convert("L"))
    im = im.convert("1")

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
    soc.send(buf)
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
