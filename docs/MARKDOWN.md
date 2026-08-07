# Markdown → printer (as-built)

Module: `markdown_renderer.py`. Endpoint: `POST /print/markdown` ([API.md](API.md)).

**This document is the as-built contract.** Prefer this file over any local scratch briefs.

## Pipeline

```text
JSON { "markdown": "…" }
  → mistune AST (plugins: table, task_lists, strikethrough, url, footnotes)
  → two-pass Pillow layout (measure height, then draw)
  → mode "1" image, width = PRINTER_WIDTH (384)
  → spool → RFCOMM
```

The renderer knows nothing about FastAPI, sockets, or Bluetooth.

## Policy

- **Not a content-quality gatekeeper.** Hideous 384px tables are fine; rejecting for aesthetics is not.
- Unknown AST nodes degrade (children / raw / textual) — do not abort the document.
- Text and QR: hard threshold, **no** Floyd–Steinberg.
- Embedded photos: `image_prep` (autocontrast / sharpen / dither) + SSRF via `net_guard`.

Ceilings at the API: `MAX_MARKDOWN_CHARS`, `MAX_RENDER_HEIGHT` (tall docs → **413**).

## Supported constructs (best-effort)

| Construct | Behaviour |
|-----------|-----------|
| Headings 1–6 | Descending font sizes |
| Paragraphs, soft/hard breaks | Wrapped runs; long unspaced strings break by character |
| Bold / italic / strike | Preserved across wraps |
| Inline + fenced code | Monospace-ish treatment |
| Lists (ol/ul), nested | Hanging indent |
| Task lists | Drawn checked/unchecked boxes |
| Blockquotes | Indent + vertical rule |
| Horizontal rules | Drawn |
| Tables | Always attempted; may shrink-to-fit if too wide |
| Links | Label + QR column when URL differs meaningfully (deduped) |
| Images | http / data / local; fallback text if fetch fails |
| Footnotes | Best-effort |
| Raw HTML | Literal code-like; not executed |
| ` ```qr ` / ` ```qrcode ` fences | QR payload = fence body |

### QR fences

````markdown
```qr
https://example.com
```
````

Integer module scale, quiet zone, pure B/W, centred, no stretch. Failure → labelled code block, not document failure.

### Tables

1. Measure intrinsic widths  
2. Allocate proportionally with a small per-column minimum  
3. Wrap cells; row height = tallest cell  
4. If minima exceed page width → render wider then uniformly shrink  

Do not rewrite tables into key/value stacks.

## Dependencies

`mistune`, `qrcode` (pinned in `requirements.txt`). No Chromium / WeasyPrint / PDF.

## Tests

`tests/test_markdown_renderer.py` — kitchen-sink fixture, width/mode/height invariants, wide table, QR binary geometry. Golden PNG for visual regression with bundled font.

## Out of scope (still)

Jinja/templating, Markdown composition for callers, Grocy/shopping integrations, browser preview UI, a separate `/print/qr` route, Bluetooth protocol changes.
