"""Tests for markdown_renderer MVP."""
from pathlib import Path

from markdown_renderer import render_markdown

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
    # Must have wrapped to multiple lines
    assert img.size[1] > 40


def test_unknown_does_not_crash():
    # raw HTML / odd constructs should not abort
    img = render_markdown("Hello <b>there</b>\n\nFoo", width=WIDTH, font_path=FONT)
    assert img.mode == "1"
