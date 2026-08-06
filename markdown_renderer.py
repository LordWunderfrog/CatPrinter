"""
Markdown → printable Pillow image (Mistune AST + measured layout).

Knows nothing about Bluetooth, FastAPI, or callers.

Supports: headings, paragraphs/breaks, emphasis, code, lists/tasks, blockquotes,
HR, links (label + end-of-paragraph QR), QR fences, tables, images (fetch when possible).
"""
from __future__ import annotations

import base64
import io
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import mistune
import PIL.Image
import PIL.ImageDraw
import PIL.ImageEnhance
import PIL.ImageFilter
import PIL.ImageFont
import PIL.ImageOps
import qrcode

# ---------------------------------------------------------------------------
# Styles — swap sizes/paths later without touching layout logic
# ---------------------------------------------------------------------------

DEFAULT_SIZES: dict[str, int] = {
    "h1": 36,
    "h2": 30,
    "h3": 26,
    "h4": 22,
    "h5": 20,
    "h6": 18,
    "body": 18,
    "code": 16,
    "quote": 18,
}

IMAGE_TIMEOUT_S = 5.0
IMAGE_MAX_BYTES = 2_000_000


@dataclass
class StyleSheet:
    """Font roles for Markdown elements. All roles may share one TTF for now."""

    font_path: str
    sizes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SIZES))
    margin: int = 8
    line_gap: int = 4
    block_gap: int = 10
    indent_step: int = 22
    quote_bar_width: int = 3
    quote_pad: int = 8
    threshold: int = 180
    table_cell_pad: int = 4
    table_min_col: int = 24
    qr_gutter: int = 8
    qr_columns: int = 2

    def size(self, role: str) -> int:
        return self.sizes.get(role, self.sizes["body"])


@dataclass(frozen=True)
class RunStyle:
    role: str = "body"
    bold: bool = False
    italic: bool = False
    strike: bool = False


@dataclass
class Run:
    text: str
    style: RunStyle


@dataclass
class InlineExtras:
    """Side effects collected while walking inline nodes."""

    links: list[str] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)  # (url, alt)


# ---------------------------------------------------------------------------
# Font cache
# ---------------------------------------------------------------------------


class FontCache:
    def __init__(self, font_path: str):
        self.font_path = font_path
        self._cache: dict[int, PIL.ImageFont.FreeTypeFont] = {}

    def get(self, size: int) -> PIL.ImageFont.FreeTypeFont:
        if size not in self._cache:
            self._cache[size] = PIL.ImageFont.truetype(self.font_path, size)
        return self._cache[size]


DrawFn = Callable[[PIL.ImageDraw.ImageDraw, PIL.Image.Image], None]


@dataclass
class LayoutResult:
    width: int
    height: int
    ops: list[DrawFn]


# ---------------------------------------------------------------------------
# Parser / QR / images
# ---------------------------------------------------------------------------


def _parser() -> mistune.Markdown:
    return mistune.create_markdown(
        renderer="ast",
        plugins=["strikethrough", "task_lists", "table", "url"],
    )


def make_qr_image(payload: str, max_side: int) -> PIL.Image.Image | None:
    """
    Build a crisp B/W QR with integer module scaling and quiet zone.
    Returns None if payload cannot be encoded.
    """
    payload = payload.strip()
    if not payload or max_side < 16:
        return None
    try:
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        modules = qr.modules_count + qr.border * 2
        box = max(1, max_side // modules)
        qr.box_size = box
        img = qr.make_image(fill_color="black", back_color="white").convert("L")
        # Force pure binary
        return img.point(lambda p: 0 if p < 128 else 255, mode="L")
    except Exception:
        return None


def load_image(src: str) -> PIL.Image.Image | None:
    """Load image from http(s), data URI, or local path. None on failure."""
    if not src:
        return None
    try:
        if src.startswith("data:"):
            return _load_data_uri(src)
        parsed = urlparse(src)
        if parsed.scheme in ("http", "https"):
            return _load_http(src)
        path = Path(src)
        if path.is_file():
            img = PIL.Image.open(path)
            img.load()
            return img.convert("RGB")
    except Exception:
        return None
    return None


def _load_data_uri(src: str) -> PIL.Image.Image | None:
    match = re.match(r"data:image/[^;]+;base64,(.+)$", src, re.DOTALL)
    if not match:
        return None
    raw = base64.b64decode(match.group(1), validate=False)
    if len(raw) > IMAGE_MAX_BYTES:
        return None
    img = PIL.Image.open(io.BytesIO(raw))
    img.load()
    return img.convert("RGB")


def _load_http(url: str) -> PIL.Image.Image | None:
    req = urllib.request.Request(url, headers={"User-Agent": "CatPrinter/1.0"})
    with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT_S) as resp:
        data = resp.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        return None
    img = PIL.Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def _fit_image(im: PIL.Image.Image, max_width: int) -> PIL.Image.Image:
    if im.width <= max_width:
        return im
    height = max(1, int(im.height * (max_width / im.width)))
    return im.resize((max_width, height), PIL.Image.Resampling.LANCZOS)


def prepare_raster_image(im: PIL.Image.Image, max_width: int) -> PIL.Image.Image:
    """
    Photo/meme path: fit, autocontrast, sharpen, light contrast, Floyd–Steinberg.
    Returns an L image with only 0/255 values (safe to paste onto the page canvas).
    Text/QR stay hard-thresholded elsewhere — do not use this for those.
    """
    fitted = _fit_image(im.convert("RGB"), max_width)
    gray = PIL.ImageOps.grayscale(fitted)
    gray = PIL.ImageOps.autocontrast(gray, cutoff=2)
    gray = gray.filter(PIL.ImageFilter.SHARPEN)
    gray = PIL.ImageEnhance.Contrast(gray).enhance(1.15)
    bw = gray.convert("1", dither=PIL.Image.Dither.FLOYDSTEINBERG)
    return bw.convert("L")


# ---------------------------------------------------------------------------
# Inline → runs
# ---------------------------------------------------------------------------


def _merge_style(base: RunStyle, **kwargs: Any) -> RunStyle:
    data = {
        "role": base.role,
        "bold": base.bold,
        "italic": base.italic,
        "strike": base.strike,
    }
    data.update(kwargs)
    return RunStyle(**data)


def _inline_runs(
    nodes: Iterable[dict] | None,
    base: RunStyle,
    extras: InlineExtras | None = None,
) -> list[Run]:
    if not nodes:
        return []
    runs: list[Run] = []
    for node in nodes:
        ntype = node.get("type")
        if ntype == "text":
            runs.append(Run(node.get("raw", ""), base))
        elif ntype == "softbreak":
            runs.append(Run(" ", base))
        elif ntype == "linebreak":
            runs.append(Run("\n", base))
        elif ntype == "strong":
            runs.extend(
                _inline_runs(node.get("children"), _merge_style(base, bold=True), extras)
            )
        elif ntype == "emphasis":
            runs.extend(
                _inline_runs(node.get("children"), _merge_style(base, italic=True), extras)
            )
        elif ntype == "strikethrough":
            runs.extend(
                _inline_runs(node.get("children"), _merge_style(base, strike=True), extras)
            )
        elif ntype == "codespan":
            runs.append(Run(node.get("raw", ""), _merge_style(base, role="code")))
        elif ntype == "link":
            url = (node.get("attrs") or {}).get("url") or ""
            runs.extend(_inline_runs(node.get("children"), base, extras))
            if extras is not None and url:
                extras.links.append(url)
        elif ntype == "image":
            attrs = node.get("attrs") or {}
            url = attrs.get("url") or ""
            alt = attrs.get("alt") or ""
            if not alt:
                alt = " ".join(
                    c.get("raw", "")
                    for c in (node.get("children") or [])
                    if c.get("type") == "text"
                )
            if extras is not None and url:
                extras.images.append((url, alt))
            elif alt:
                runs.append(Run(alt, base))
        elif ntype in ("raw", "inline_html"):
            runs.append(Run(node.get("raw", ""), _merge_style(base, role="code")))
        else:
            if node.get("children"):
                runs.extend(_inline_runs(node["children"], base, extras))
            elif "raw" in node:
                runs.append(Run(str(node["raw"]), base))
    return runs


# ---------------------------------------------------------------------------
# Measuring / wrapping
# ---------------------------------------------------------------------------


def _font_for(cache: FontCache, styles: StyleSheet, style: RunStyle) -> PIL.ImageFont.FreeTypeFont:
    return cache.get(styles.size(style.role))


def _run_width(cache: FontCache, styles: StyleSheet, run: Run) -> float:
    if not run.text or run.text == "\n":
        return 0.0
    font = _font_for(cache, styles, run.style)
    extra = 1 if run.style.bold else 0
    return font.getlength(run.text) + extra


def _run_height(cache: FontCache, styles: StyleSheet, run: Run) -> int:
    font = _font_for(cache, styles, run.style)
    ascent, descent = font.getmetrics()
    return ascent + descent


def _split_run_at_width(
    cache: FontCache, styles: StyleSheet, run: Run, max_width: float
) -> tuple[Run, Run | None]:
    if max_width <= 0:
        return Run("", run.style), run
    text = run.text
    if _run_width(cache, styles, run) <= max_width:
        return run, None

    lo, hi = 1, len(text)
    fit = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        piece = Run(text[:mid], run.style)
        if _run_width(cache, styles, piece) <= max_width:
            fit = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if fit == 0:
        fit = 1
    return Run(text[:fit], run.style), Run(text[fit:], run.style)


def _wrap_runs(
    cache: FontCache,
    styles: StyleSheet,
    runs: list[Run],
    max_width: float,
) -> list[list[Run]]:
    lines: list[list[Run]] = []
    current: list[Run] = []
    used = 0.0

    def commit() -> None:
        nonlocal current, used
        if current:
            lines.append(current)
        current = []
        used = 0.0

    def append_piece(piece: Run) -> None:
        nonlocal used
        if not piece.text:
            return
        current.append(piece)
        used += _run_width(cache, styles, piece)

    for run in runs:
        if run.text == "\n":
            commit()
            continue
        if not run.text:
            continue

        remaining: Run | None = run
        while remaining and remaining.text:
            avail = max_width - used
            if avail < 1 and used > 0:
                commit()
                continue

            if _run_width(cache, styles, remaining) <= avail:
                append_piece(remaining)
                break

            text = remaining.text
            if " " in text:
                words = text.split(" ")
                acc = ""
                consumed = 0
                for wi, word in enumerate(words):
                    trial = word if not acc else f"{acc} {word}"
                    if _run_width(cache, styles, Run(trial, remaining.style)) <= avail:
                        acc = trial
                        consumed = wi + 1
                    else:
                        break
                if acc:
                    append_piece(Run(acc, remaining.style))
                    rest = " ".join(words[consumed:])
                    commit()
                    remaining = Run(rest, remaining.style) if rest else None
                    continue
                if used > 0:
                    commit()
                    continue

            first, rest = _split_run_at_width(
                cache, styles, remaining, avail if avail >= 1 else max_width
            )
            if not first.text and rest is not None:
                commit()
                continue
            append_piece(first)
            commit()
            remaining = rest

    commit()
    return lines or [[]]


def _line_height(cache: FontCache, styles: StyleSheet, line: list[Run]) -> int:
    if not line:
        return styles.size("body")
    return max(_run_height(cache, styles, r) for r in line) + styles.line_gap


def _draw_run(
    draw: PIL.ImageDraw.ImageDraw,
    img: PIL.Image.Image,
    x: float,
    y: float,
    run: Run,
    cache: FontCache,
    styles: StyleSheet,
    fill: int = 0,
) -> float:
    if not run.text:
        return x
    font = _font_for(cache, styles, run.style)
    text = run.text
    w = float(font.getlength(text))

    if run.style.italic:
        bbox = font.getbbox(text)
        gw = max(bbox[2] - bbox[0], 1) + 2
        gh = max(bbox[3] - bbox[1], 1) + 2
        pad = max(gh // 3, 4)
        tmp = PIL.Image.new("L", (gw + pad, gh), 255)
        td = PIL.ImageDraw.Draw(tmp)
        td.text((0, -bbox[1]), text, font=font, fill=0)
        if run.style.bold:
            td.text((1, -bbox[1]), text, font=font, fill=0)
        tmp = tmp.transform(
            tmp.size,
            PIL.Image.Transform.AFFINE,
            (1, 0.22, 0, 0, 1, 0),
            resample=PIL.Image.Resampling.NEAREST,
            fillcolor=255,
        )
        px, tp = img.load(), tmp.load()
        ox, oy = int(x), int(y)
        for yy in range(tmp.height):
            for xx in range(tmp.width):
                if tp[xx, yy] < 128:
                    ix, iy = ox + xx, oy + yy
                    if 0 <= ix < img.width and 0 <= iy < img.height:
                        px[ix, iy] = min(px[ix, iy], tp[xx, yy])
    else:
        draw.text((x, y), text, font=font, fill=fill)
        if run.style.bold:
            draw.text((x + 1, y), text, font=font, fill=fill)

    if run.style.strike:
        cy = y + _run_height(cache, styles, run) * 0.55
        draw.line((x, cy, x + w, cy), fill=fill, width=1)

    return x + w + (1 if run.style.bold else 0)


def _draw_line_runs(
    draw: PIL.ImageDraw.ImageDraw,
    img: PIL.Image.Image,
    x: float,
    y: float,
    line: list[Run],
    cache: FontCache,
    styles: StyleSheet,
) -> None:
    cx = x
    for run in line:
        cx = _draw_run(draw, img, cx, y, run, cache, styles)


def _paste_gray(
    dest: PIL.Image.Image, src: PIL.Image.Image, xy: tuple[int, int]
) -> None:
    """Paste grayscale (darker wins) without alpha."""
    src = src.convert("L")
    ox, oy = xy
    dp, sp = dest.load(), src.load()
    for yy in range(src.height):
        for xx in range(src.width):
            ix, iy = ox + xx, oy + yy
            if 0 <= ix < dest.width and 0 <= iy < dest.height:
                dp[ix, iy] = min(dp[ix, iy], sp[xx, yy])


# ---------------------------------------------------------------------------
# Block layout
# ---------------------------------------------------------------------------


class _Layout:
    def __init__(self, width: int, styles: StyleSheet, font_path: str):
        self.width = width
        self.styles = styles
        self.cache = FontCache(font_path)
        self.margin = styles.margin
        self.content_width = max(1, width - 2 * styles.margin)
        self.y = float(styles.margin)
        self.ops: list[DrawFn] = []
        self._seen_urls: set[str] = set()
        self._pending_table_links: list[str] = []
        self._pending_table_images: list[tuple[str, str]] = []

    def _content_left(self, indent: int = 0, quote_depth: int = 0) -> int:
        return (
            self.margin
            + indent * self.styles.indent_step
            + quote_depth * (self.styles.quote_bar_width + self.styles.quote_pad)
        )

    def _max_width(self, indent: int = 0, quote_depth: int = 0) -> float:
        left = self._content_left(indent, quote_depth)
        return float(max(1, self.width - self.margin - left))

    def add_gap(self, amount: int | None = None) -> None:
        self.y += self.styles.block_gap if amount is None else amount

    def layout_nodes(
        self, nodes: list[dict] | None, indent: int = 0, quote_depth: int = 0
    ) -> None:
        if not nodes:
            return
        for node in nodes:
            self.layout_node(node, indent, quote_depth)

    def layout_node(self, node: dict, indent: int = 0, quote_depth: int = 0) -> None:
        ntype = node.get("type")
        if ntype == "blank_line":
            self.add_gap(self.styles.line_gap)
            return
        if ntype == "heading":
            self._heading(node, indent, quote_depth)
        elif ntype == "paragraph":
            self._paragraph(node, indent, quote_depth)
        elif ntype == "block_text":
            self._paragraph(node, indent, quote_depth, role="body", gap_after=False)
        elif ntype == "list":
            self._list(node, indent, quote_depth)
        elif ntype == "block_quote":
            self._blockquote(node, indent, quote_depth)
        elif ntype == "thematic_break":
            self._hr(indent, quote_depth)
        elif ntype == "block_code":
            self._block_code(node, indent, quote_depth)
        elif ntype == "table":
            self._table(node, indent, quote_depth)
        elif ntype in ("list_item", "task_list_item"):
            self.layout_nodes(node.get("children"), indent, quote_depth)
        else:
            if node.get("children"):
                self.layout_nodes(node["children"], indent, quote_depth)
            elif "raw" in node and node["raw"]:
                self._emit_runs([Run(str(node["raw"]), RunStyle(role="code"))], indent, quote_depth)

    def _heading(self, node: dict, indent: int, quote_depth: int) -> None:
        level = int(node.get("attrs", {}).get("level", 1))
        level = min(max(level, 1), 6)
        role = f"h{level}"
        extras = InlineExtras()
        runs = _inline_runs(node.get("children"), RunStyle(role=role, bold=True), extras)
        self._emit_runs(runs, indent, quote_depth)
        self._emit_images(extras.images, indent, quote_depth)
        self._emit_link_qrs(extras.links, indent, quote_depth)
        self.add_gap()

    def _paragraph(
        self,
        node: dict,
        indent: int,
        quote_depth: int,
        role: str = "body",
        gap_after: bool = True,
    ) -> None:
        extras = InlineExtras()
        runs = _inline_runs(node.get("children"), RunStyle(role=role), extras)
        meaningful = [r for r in runs if r.text and r.text.strip()]
        if meaningful:
            self._emit_runs(runs, indent, quote_depth)
        self._emit_images(extras.images, indent, quote_depth)
        self._emit_link_qrs(extras.links, indent, quote_depth)
        if gap_after:
            self.add_gap()

    def _emit_runs(self, runs: list[Run], indent: int, quote_depth: int) -> None:
        if not runs:
            return
        left = self._content_left(indent, quote_depth)
        max_w = self._max_width(indent, quote_depth)
        lines = _wrap_runs(self.cache, self.styles, runs, max_w)
        for line in lines:
            lh = _line_height(self.cache, self.styles, line)
            y = self.y

            def make_op(line=line, x=left, y=y):
                def op(draw, img):
                    _draw_line_runs(draw, img, x, y, line, self.cache, self.styles)

                return op

            self.ops.append(make_op())
            self.y += lh

    def _emit_link_qrs(self, urls: list[str], indent: int, quote_depth: int) -> None:
        items: list[tuple[str, str]] = []
        for url in urls:
            if url in self._seen_urls:
                continue
            self._seen_urls.add(url)
            items.append((url, url))
        self._emit_qr_grid(items, indent, quote_depth)

    def _emit_qr_grid(
        self,
        items: list[tuple[str, str]],
        indent: int,
        quote_depth: int,
    ) -> None:
        """
        Pack QR + caption cells into a multi-column row layout (~50% width each
        when qr_columns=2), captions wrap under their own QR.
        """
        cleaned: list[tuple[str, str]] = []
        for payload, caption in items:
            payload = (payload or "").strip()
            if payload:
                cleaned.append((payload, (caption or payload).strip()))
        if not cleaned:
            return

        cols = max(1, self.styles.qr_columns)
        gutter = self.styles.qr_gutter
        content_w = int(self._max_width(indent, quote_depth))
        left0 = self._content_left(indent, quote_depth)
        col_w = max(16, (content_w - gutter * (cols - 1)) // cols)

        for i in range(0, len(cleaned), cols):
            row = cleaned[i : i + cols]
            self._emit_qr_row(row, left0, col_w, gutter, indent, quote_depth)

    def _emit_qr_row(
        self,
        row: list[tuple[str, str]],
        left0: int,
        col_w: int,
        gutter: int,
        indent: int,
        quote_depth: int,
    ) -> None:
        cells: list[dict[str, Any]] = []
        for payload, caption in row:
            qr = make_qr_image(payload, col_w)
            if qr is None:
                cap_runs = [Run(f"[QR failed] {payload}", RunStyle(role="code"))]
                cap_lines = _wrap_runs(self.cache, self.styles, cap_runs, float(col_w))
                cells.append({"qr": None, "lines": cap_lines, "qr_h": 0})
                continue
            cap_runs = [Run(caption, RunStyle(role="code"))] if caption else []
            cap_lines = (
                _wrap_runs(self.cache, self.styles, cap_runs, float(col_w))
                if cap_runs
                else []
            )
            cells.append({"qr": qr, "lines": cap_lines, "qr_h": qr.height})

        row_h = 0
        for cell in cells:
            h = cell["qr_h"]
            if cell["qr"] is not None and cell["lines"]:
                h += self.styles.line_gap
            h += sum(
                _line_height(self.cache, self.styles, ln) for ln in cell["lines"]
            )
            row_h = max(row_h, h)

        y0 = self.y
        for ci, cell in enumerate(cells):
            cell_left = left0 + ci * (col_w + gutter)
            y = y0
            qr = cell["qr"]
            if qr is not None:
                qx = cell_left + max(0, (col_w - qr.width) // 2)

                def make_qr_op(qr=qr, qx=qx, y=y):
                    def op(draw, img):
                        _paste_gray(img, qr, (qx, int(y)))

                    return op

                self.ops.append(make_qr_op())
                y += qr.height + (self.styles.line_gap if cell["lines"] else 0)

            for line in cell["lines"]:
                lh = _line_height(self.cache, self.styles, line)

                def make_text_op(line=line, x=cell_left, y=y):
                    def op(draw, img):
                        _draw_line_runs(
                            draw, img, x, y, line, self.cache, self.styles
                        )

                    return op

                self.ops.append(make_text_op())
                y += lh

        self.y = y0 + row_h + self.styles.line_gap

    def _emit_images(
        self, images: list[tuple[str, str]], indent: int, quote_depth: int
    ) -> None:
        for url, alt in images:
            self._emit_image(url, alt, indent, quote_depth)

    def _emit_image(self, url: str, alt: str, indent: int, quote_depth: int) -> None:
        max_w = int(self._max_width(indent, quote_depth))
        left = self._content_left(indent, quote_depth)
        loaded = load_image(url)
        if loaded is None:
            if alt:
                self._emit_runs(
                    [Run(f"[image: {alt}]", RunStyle(role="code"))],
                    indent,
                    quote_depth,
                )
            return

        gray = prepare_raster_image(loaded, max_w)
        x = left + max(0, (max_w - gray.width) // 2)
        y = self.y

        def op(draw, img, gray=gray, x=x, y=y):
            _paste_gray(img, gray, (x, int(y)))

        self.ops.append(op)
        self.y += gray.height + self.styles.line_gap
        if alt:
            self._emit_runs([Run(alt, RunStyle(role="code"))], indent, quote_depth)
        self.add_gap(self.styles.line_gap)

    def _hr(self, indent: int, quote_depth: int) -> None:
        left = self._content_left(indent, quote_depth)
        right = self.width - self.margin
        y = self.y + 4

        def op(draw, img, left=left, right=right, y=y):
            draw.line((left, y, right, y), fill=0, width=2)

        self.ops.append(op)
        self.y = y + 8
        self.add_gap()

    def _block_code(self, node: dict, indent: int, quote_depth: int) -> None:
        info = ((node.get("attrs") or {}).get("info") or "").strip()
        first = info.split()[0].lower() if info else ""
        raw = node.get("raw", "")
        if raw.endswith("\n"):
            raw = raw[:-1]

        if first in ("qr", "qrcode"):
            payload = raw.strip()
            self._seen_urls.add(payload)
            self._emit_qr_grid([(payload, payload)], indent, quote_depth)
            self.add_gap()
            return

        lines = raw.split("\n") if raw else [""]
        left = self._content_left(indent, quote_depth)
        max_w = self._max_width(indent, quote_depth)
        style = RunStyle(role="code")
        for line_text in lines:
            runs = [Run(line_text, style)] if line_text else [Run(" ", style)]
            wrapped = _wrap_runs(self.cache, self.styles, runs, max_w)
            for line in wrapped:
                lh = _line_height(self.cache, self.styles, line)
                y = self.y

                def make_op(line=line, x=left, y=y):
                    def op(draw, img):
                        _draw_line_runs(draw, img, x, y, line, self.cache, self.styles)

                    return op

                self.ops.append(make_op())
                self.y += lh
        self.add_gap()

    def _blockquote(self, node: dict, indent: int, quote_depth: int) -> None:
        start_y = self.y
        self.layout_nodes(node.get("children"), indent, quote_depth + 1)
        end_y = self.y
        bar_x = self._content_left(indent, quote_depth)
        width = self.styles.quote_bar_width

        def op(draw, img, bar_x=bar_x, start_y=start_y, end_y=end_y, width=width):
            draw.rectangle(
                (bar_x, start_y, bar_x + width, max(end_y, start_y + 1)), fill=0
            )

        self.ops.append(op)
        self.add_gap()

    def _list(self, node: dict, indent: int, quote_depth: int) -> None:
        ordered = bool(node.get("attrs", {}).get("ordered"))
        index = 1
        for child in node.get("children") or []:
            ctype = child.get("type")
            if ctype == "task_list_item":
                self._task_item(child, indent, quote_depth)
            elif ctype == "list_item":
                marker = f"{index}. " if ordered else "• "
                self._list_item(child, indent, quote_depth, marker)
                index += 1
            else:
                self.layout_node(child, indent, quote_depth)
        self.add_gap()

    def _list_item(self, node: dict, indent: int, quote_depth: int, marker: str) -> None:
        left = self._content_left(indent, quote_depth)
        marker_run = Run(marker, RunStyle(role="body", bold=True))
        marker_w = _run_width(self.cache, self.styles, marker_run)
        children = list(node.get("children") or [])
        y0 = self.y

        def draw_marker(draw, img, x=left, y=y0, run=marker_run):
            _draw_run(draw, img, x, y, run, self.cache, self.styles)

        self.ops.append(draw_marker)

        if not children:
            self.y += _run_height(self.cache, self.styles, marker_run) + self.styles.line_gap
            return

        first, *rest = children
        if first.get("type") in ("block_text", "paragraph"):
            extras = InlineExtras()
            runs = _inline_runs(first.get("children"), RunStyle(role="body"), extras)
            max_w = self._max_width(indent, quote_depth) - marker_w
            lines = _wrap_runs(self.cache, self.styles, runs, max(1.0, max_w))
            for line in lines:
                lh = _line_height(self.cache, self.styles, line)
                x = left + marker_w
                y = self.y

                def make_op(line=line, x=x, y=y):
                    def op(draw, img):
                        _draw_line_runs(draw, img, x, y, line, self.cache, self.styles)

                    return op

                self.ops.append(make_op())
                self.y += lh
            self._emit_images(extras.images, indent + 1, quote_depth)
            self._emit_link_qrs(extras.links, indent + 1, quote_depth)
            for sub in rest:
                self.layout_node(sub, indent + 1, quote_depth)
        else:
            self.y += _run_height(self.cache, self.styles, marker_run) + self.styles.line_gap
            for sub in children:
                self.layout_node(sub, indent + 1, quote_depth)

    def _task_item(self, node: dict, indent: int, quote_depth: int) -> None:
        checked = bool(node.get("attrs", {}).get("checked"))
        left = self._content_left(indent, quote_depth)
        box = 14
        y0 = self.y

        def draw_box(draw, img, x=left, y=y0, checked=checked, box=box):
            draw.rectangle((x, y + 2, x + box, y + 2 + box), outline=0, width=2)
            if checked:
                draw.line((x + 3, y + 2 + box // 2, x + box // 2, y + box), fill=0, width=2)
                draw.line((x + box // 2, y + box, x + box - 2, y + 4), fill=0, width=2)

        self.ops.append(draw_box)
        marker_w = box + 8
        children = list(node.get("children") or [])
        if children and children[0].get("type") in ("block_text", "paragraph"):
            first, *rest = children
            extras = InlineExtras()
            runs = _inline_runs(first.get("children"), RunStyle(role="body"), extras)
            max_w = self._max_width(indent, quote_depth) - marker_w
            lines = _wrap_runs(self.cache, self.styles, runs, max(1.0, max_w))
            for line in lines:
                lh = _line_height(self.cache, self.styles, line)
                x = left + marker_w
                y = self.y

                def make_op(line=line, x=x, y=y):
                    def op(draw, img):
                        _draw_line_runs(draw, img, x, y, line, self.cache, self.styles)

                    return op

                self.ops.append(make_op())
                self.y += lh
            self._emit_images(extras.images, indent + 1, quote_depth)
            self._emit_link_qrs(extras.links, indent + 1, quote_depth)
            for sub in rest:
                self.layout_node(sub, indent + 1, quote_depth)
        else:
            self.y += box + self.styles.line_gap + 4
            for sub in children:
                self.layout_node(sub, indent + 1, quote_depth)

    # ----- tables -----

    def _table(self, node: dict, indent: int, quote_depth: int) -> None:
        rows: list[list[list[Run]]] = []

        for child in node.get("children") or []:
            if child.get("type") == "table_head":
                row_cells = []
                for cell in child.get("children") or []:
                    if cell.get("type") != "table_cell":
                        continue
                    extras = InlineExtras()
                    runs = _inline_runs(
                        cell.get("children"), RunStyle(role="body", bold=True), extras
                    )
                    row_cells.append(runs)
                    self._pending_table_links.extend(extras.links)
                    self._pending_table_images.extend(extras.images)
                rows.append(row_cells)
            elif child.get("type") == "table_body":
                for row in child.get("children") or []:
                    if row.get("type") != "table_row":
                        continue
                    row_cells = []
                    for cell in row.get("children") or []:
                        if cell.get("type") != "table_cell":
                            continue
                        extras = InlineExtras()
                        runs = _inline_runs(
                            cell.get("children"), RunStyle(role="body"), extras
                        )
                        row_cells.append(runs)
                        self._pending_table_links.extend(extras.links)
                        self._pending_table_images.extend(extras.images)
                    rows.append(row_cells)

        if not rows:
            return

        cols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < cols:
                r.append([])

        pad = self.styles.table_cell_pad
        min_col = self.styles.table_min_col
        max_w = int(self._max_width(indent, quote_depth))

        intrinsic = [min_col] * cols
        for r in rows:
            for ci, runs in enumerate(r):
                w = sum(_run_width(self.cache, self.styles, run) for run in runs) + 2 * pad
                intrinsic[ci] = max(intrinsic[ci], int(w), min_col)

        total_intr = sum(intrinsic)
        if total_intr <= max_w:
            col_widths = list(intrinsic)
            slack = max_w - total_intr
            if slack and total_intr:
                for i in range(cols):
                    col_widths[i] += int(slack * (intrinsic[i] / total_intr))
                col_widths[0] += max_w - sum(col_widths)
            table_w = sum(col_widths)
            scale = 1.0
        else:
            col_widths = list(intrinsic)
            table_w = total_intr
            scale = max_w / table_w

        wrapped_rows: list[list[list[list[Run]]]] = []
        row_heights: list[int] = []
        for r in rows:
            cell_lines: list[list[list[Run]]] = []
            rh = 0
            for ci, runs in enumerate(r):
                cw = max(1.0, col_widths[ci] - 2 * pad)
                lines = _wrap_runs(self.cache, self.styles, runs, cw) if runs else [[]]
                cell_lines.append(lines)
                h = sum(
                    _line_height(self.cache, self.styles, ln) for ln in lines
                ) or self.styles.size("body")
                rh = max(rh, h + 2 * pad)
            wrapped_rows.append(cell_lines)
            row_heights.append(rh)

        table_h = sum(row_heights) + 1
        table_img = PIL.Image.new("L", (table_w, table_h), 255)
        tdraw = PIL.ImageDraw.Draw(table_img)

        y = 0
        for ri, cell_lines in enumerate(wrapped_rows):
            x = 0
            rh = row_heights[ri]
            for ci, lines in enumerate(cell_lines):
                cw = col_widths[ci]
                tdraw.rectangle((x, y, x + cw, y + rh), outline=0, width=1)
                cy = y + pad
                for line in lines:
                    _draw_line_runs(
                        tdraw, table_img, x + pad, cy, line, self.cache, self.styles
                    )
                    cy += _line_height(self.cache, self.styles, line)
                x += cw
            y += rh

        if scale < 1.0 - 1e-6:
            new_w = max(1, int(table_w * scale))
            new_h = max(1, int(table_h * scale))
            table_img = table_img.resize((new_w, new_h), PIL.Image.Resampling.NEAREST)

        left = self._content_left(indent, quote_depth)
        paste_y = self.y

        def op(draw, img, table_img=table_img, left=left, paste_y=paste_y):
            _paste_gray(img, table_img, (left, int(paste_y)))

        self.ops.append(op)
        self.y += table_img.height + self.styles.block_gap

        pending_links = self._pending_table_links
        pending_images = self._pending_table_images
        self._pending_table_links = []
        self._pending_table_images = []
        self._emit_images(pending_images, indent, quote_depth)
        self._emit_link_qrs(pending_links, indent, quote_depth)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def layout_markdown(
    markdown: str,
    width: int,
    font_path: str,
    styles: StyleSheet | None = None,
) -> LayoutResult:
    styles = styles or StyleSheet(font_path=font_path)
    if not styles.font_path:
        styles.font_path = font_path
    ast = _parser()(markdown)
    if not isinstance(ast, list):
        ast = []
    lay = _Layout(width=width, styles=styles, font_path=styles.font_path)
    lay.layout_nodes(ast)
    height = max(int(lay.y + styles.margin), styles.margin * 2 + 1)
    return LayoutResult(width=width, height=height, ops=lay.ops)


def render_markdown(
    markdown: str,
    width: int,
    font_path: str,
    styles: StyleSheet | None = None,
) -> PIL.Image.Image:
    """Return an exact-width, 1-bit printable image."""
    styles = styles or StyleSheet(font_path=font_path)
    layout = layout_markdown(markdown, width, font_path, styles)

    gray = PIL.Image.new("L", (layout.width, layout.height), 255)
    draw = PIL.ImageDraw.Draw(gray)
    for op in layout.ops:
        op(draw, gray)

    return gray.point(lambda p: 0 if p < styles.threshold else 255, mode="1")
