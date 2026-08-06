"""
Markdown → printable Pillow image (Mistune AST + measured layout).

Knows nothing about Bluetooth, FastAPI, or callers.
MVP: headings, paragraphs/breaks, emphasis, code, lists/tasks, blockquotes, HR.
Links render as their label text only (QR pass comes later).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import mistune
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

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


@dataclass
class StyleSheet:
    """Font roles for Markdown elements. All roles may share one TTF for MVP."""

    font_path: str
    sizes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SIZES))
    margin: int = 8
    line_gap: int = 4
    block_gap: int = 10
    indent_step: int = 22
    quote_bar_width: int = 3
    quote_pad: int = 8
    threshold: int = 180

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


# ---------------------------------------------------------------------------
# Draw operations (pass 1 records; pass 2 executes)
# ---------------------------------------------------------------------------

DrawFn = Callable[[PIL.ImageDraw.ImageDraw, PIL.Image.Image], None]


@dataclass
class LayoutResult:
    width: int
    height: int
    ops: list[DrawFn]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parser() -> mistune.Markdown:
    return mistune.create_markdown(
        renderer="ast",
        plugins=["strikethrough", "task_lists"],
    )


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


def _inline_runs(nodes: Iterable[dict] | None, base: RunStyle) -> list[Run]:
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
            runs.extend(_inline_runs(node.get("children"), _merge_style(base, bold=True)))
        elif ntype == "emphasis":
            runs.extend(_inline_runs(node.get("children"), _merge_style(base, italic=True)))
        elif ntype == "strikethrough":
            runs.extend(_inline_runs(node.get("children"), _merge_style(base, strike=True)))
        elif ntype == "codespan":
            runs.append(
                Run(node.get("raw", ""), _merge_style(base, role="code"))
            )
        elif ntype == "link":
            # MVP: label only; href ignored until QR pass
            runs.extend(_inline_runs(node.get("children"), base))
        elif ntype == "image":
            alt = node.get("attrs", {}).get("alt") or ""
            if not alt:
                alt = " ".join(
                    c.get("raw", "")
                    for c in (node.get("children") or [])
                    if c.get("type") == "text"
                )
            if alt:
                runs.append(Run(alt, base))
        elif ntype in ("raw", "inline_html"):
            runs.append(Run(node.get("raw", ""), _merge_style(base, role="code")))
        else:
            # Unknown inline: degrade to children or raw
            if node.get("children"):
                runs.extend(_inline_runs(node["children"], base))
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
    # bold simulation draws an extra pixel
    extra = 1 if run.style.bold else 0
    return font.getlength(run.text) + extra


def _run_height(cache: FontCache, styles: StyleSheet, run: Run) -> int:
    font = _font_for(cache, styles, run.style)
    ascent, descent = font.getmetrics()
    return ascent + descent


def _split_run_at_width(
    cache: FontCache, styles: StyleSheet, run: Run, max_width: float
) -> tuple[Run, Run | None]:
    """Split run so the first piece fits max_width (character boundaries)."""
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
        # Force at least one character to avoid infinite loops
        fit = 1
    return Run(text[:fit], run.style), Run(text[fit:], run.style)


def _wrap_runs(
    cache: FontCache,
    styles: StyleSheet,
    runs: list[Run],
    max_width: float,
) -> list[list[Run]]:
    """Wrap styled runs into lines that fit max_width."""
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
            # Word-aware wrap when possible
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

            # Character split (overlong token / no spaces)
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

# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


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
    """Draw one run; return x advance. Bold/italic faked when TTF has no faces."""
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
        self._list_counters: list[int] = []

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
        elif ntype in ("list_item", "task_list_item"):
            # Handled by _list; if orphaned, degrade
            self.layout_nodes(node.get("children"), indent, quote_depth)
        else:
            if node.get("children"):
                self.layout_nodes(node["children"], indent, quote_depth)
            elif "raw" in node and node["raw"]:
                runs = [Run(str(node["raw"]), RunStyle(role="code"))]
                self._emit_runs(runs, indent, quote_depth)

    def _heading(self, node: dict, indent: int, quote_depth: int) -> None:
        level = int(node.get("attrs", {}).get("level", 1))
        level = min(max(level, 1), 6)
        role = f"h{level}"
        runs = _inline_runs(node.get("children"), RunStyle(role=role, bold=True))
        self._emit_runs(runs, indent, quote_depth)
        self.add_gap()

    def _paragraph(
        self,
        node: dict,
        indent: int,
        quote_depth: int,
        role: str = "body",
        gap_after: bool = True,
    ) -> None:
        runs = _inline_runs(node.get("children"), RunStyle(role=role))
        self._emit_runs(runs, indent, quote_depth)
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
        raw = node.get("raw", "")
        if raw.endswith("\n"):
            raw = raw[:-1]
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
            draw.rectangle((bar_x, start_y, bar_x + width, max(end_y, start_y + 1)), fill=0)

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
        # First block_text on same line as marker; nested blocks indented
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
            role = "body"
            runs = _inline_runs(first.get("children"), RunStyle(role=role))
            # width reduced by marker
            max_w = self._max_width(indent, quote_depth) - marker_w
            lines = _wrap_runs(self.cache, self.styles, runs, max(1.0, max_w))
            for i, line in enumerate(lines):
                lh = _line_height(self.cache, self.styles, line)
                x = left + marker_w if i == 0 else left + marker_w
                y = self.y

                def make_op(line=line, x=x, y=y):
                    def op(draw, img):
                        _draw_line_runs(draw, img, x, y, line, self.cache, self.styles)

                    return op

                self.ops.append(make_op())
                self.y += lh
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
                # simple check mark
                draw.line((x + 3, y + 2 + box // 2, x + box // 2, y + box), fill=0, width=2)
                draw.line((x + box // 2, y + box, x + box - 2, y + 4), fill=0, width=2)

        self.ops.append(draw_box)
        marker_w = box + 8
        children = list(node.get("children") or [])
        if children and children[0].get("type") in ("block_text", "paragraph"):
            first, *rest = children
            runs = _inline_runs(first.get("children"), RunStyle(role="body"))
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
            for sub in rest:
                self.layout_node(sub, indent + 1, quote_depth)
        else:
            self.y += box + self.styles.line_gap + 4
            for sub in children:
                self.layout_node(sub, indent + 1, quote_depth)


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
    """
    Return an exact-width, 1-bit printable image.
    """
    styles = styles or StyleSheet(font_path=font_path)
    layout = layout_markdown(markdown, width, font_path, styles)

    gray = PIL.Image.new("L", (layout.width, layout.height), 255)
    draw = PIL.ImageDraw.Draw(gray)
    for op in layout.ops:
        op(draw, gray)

    # Explicit threshold → 1-bit (no dither)
    bw = gray.point(lambda p: 0 if p < styles.threshold else 255, mode="1")
    return bw
