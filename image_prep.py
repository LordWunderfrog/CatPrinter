"""
Image prep for thermal printing (no Bluetooth / sockets).
Shared by Markdown embeds, API uploads, and CLI.
"""
from __future__ import annotations

import PIL.Image
import PIL.ImageEnhance
import PIL.ImageFilter
import PIL.ImageOps


def prepare_raster_image(
    im: PIL.Image.Image,
    max_width: int,
) -> PIL.Image.Image:
    """
    Photo/meme prep: EXIF-orient, fit, autocontrast, sharpen, contrast, Floyd–Steinberg.
    Returns an L image with only 0/255 values.
    Not for text or QR.
    """
    rgb = PIL.ImageOps.exif_transpose(im.convert("RGB"))
    if rgb.width > max_width:
        height = max(1, int(rgb.height * (max_width / rgb.width)))
        rgb = rgb.resize((max_width, height), PIL.Image.Resampling.LANCZOS)
    gray = PIL.ImageOps.grayscale(rgb)
    gray = PIL.ImageOps.autocontrast(gray, cutoff=2)
    gray = gray.filter(PIL.ImageFilter.SHARPEN)
    gray = PIL.ImageEnhance.Contrast(gray).enhance(1.15)
    bw = gray.convert("1", dither=PIL.Image.Dither.FLOYDSTEINBERG)
    return bw.convert("L")
