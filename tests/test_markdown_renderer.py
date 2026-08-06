"""Tests for markdown_renderer."""
from pathlib import Path

import PIL.Image

from markdown_renderer import make_qr_image, prepare_raster_image, render_markdown

ROOT = Path(__file__).resolve().parents[1]
FONT = str(ROOT / "Lucon.ttf")
WIDTH = 384

MVP_DOC = """# Shopping

## Tonight

**Bold** and *italic* and ~~nope~~ plus `code`.

- milk
- eggs
  - free range
- [ ] butter
- [x] bread

1. Preheat
2. Cook

> Do not forget bags

---

```
plain fence
line two
```

A paragraph with a [link label](https://example.com) only.

SupercalifragilisticexpialidociousSupercalifragilisticexpialidocious
"""

PASS2_DOC = """# Pass 2

See [one](https://a.example/x) and [two](https://a.example/x) and https://b.example/y

```qr
https://qr.example/z
```

| A | B | C | D | E | F | G |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| aa | bb | cc | dd | ee | ff | gg |
"""


def test_render_width_mode_height():
    img = render_markdown(MVP_DOC, width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.mode == "1"
    assert img.size[1] > 50
    assert img.size[1] < 5000


def test_emptyish_still_prints():
    img = render_markdown("x", width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.mode == "1"


def test_long_string_stays_in_bounds():
    img = render_markdown("A" * 500, width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.size[1] > 40


def test_unknown_does_not_crash():
    img = render_markdown("Hello <b>there</b>\n\nFoo", width=WIDTH, font_path=FONT)
    assert img.mode == "1"


def test_pass2_links_qr_table():
    img = render_markdown(PASS2_DOC, width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.mode == "1"
    # QRs + table make it taller than plain text
    assert img.size[1] > 200


def test_qr_modules_binary_square():
    qr = make_qr_image("https://example.com", max_side=200)
    assert qr is not None
    assert qr.width == qr.height
    extrema = qr.getextrema()
    assert extrema[0] == 0 and extrema[1] == 255


def test_qr_two_column_shorter_than_stacked_estimate():
    """Two distinct links in one paragraph should share a row (half-width each)."""
    md = "Go [a](https://a.example/1) and [b](https://b.example/2)\n"
    img = render_markdown(md, width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.mode == "1"
    # One row of half-width QRs should be much shorter than two full-width stacks (~700+)
    assert img.size[1] < 500


def test_raster_image_uses_dither_not_flat_threshold(tmp_path):
    """Embedded photos should retain mid-tone speckles (Floyd), not posterize."""
    # Synthetic gradient — threshold would be two slabs; dither has mixed pixels
    grad = PIL.Image.new("L", (120, 40))
    gp = grad.load()
    for x in range(120):
        for y in range(40):
            gp[x, y] = int(255 * x / 119)
    path = tmp_path / "grad.png"
    grad.save(path)

    out = prepare_raster_image(grad.convert("RGB"), max_width=120)
    assert out.mode == "L"
    # Should contain both black and white after dither
    extrema = out.getextrema()
    assert extrema[0] == 0 and extrema[1] == 255
    # Mid band should not be a single solid value
    mid = [out.getpixel((x, 20)) for x in range(50, 70)]
    assert len(set(mid)) > 1


def test_wide_table_not_rejected():
    md = "| " + " | ".join(f"C{i}" for i in range(7)) + " |\n"
    md += "| " + " | ".join("---" for _ in range(7)) + " |\n"
    md += "| " + " | ".join("x" * 8 for _ in range(7)) + " |\n"
    img = render_markdown(md, width=WIDTH, font_path=FONT)
    assert img.size[0] == WIDTH
    assert img.mode == "1"
