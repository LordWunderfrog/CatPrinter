#!/usr/bin/env bash
# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/  ->  copy onto HAOS /addons/cat_printer
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/cat_printer"

rm -rf "$OUT"
mkdir -p "$OUT"

cp "$ROOT/yhk_printer.py" "$ROOT/image_prep.py" "$ROOT/reddit_image.py" "$ROOT/api.py" "$ROOT/cat-printer.py" "$ROOT/markdown_renderer.py" "$ROOT/requirements.txt" "$ROOT/Lucon.ttf" "$OUT/"
cp "$ROOT/ha-addon/config.yaml" "$ROOT/ha-addon/Dockerfile" "$ROOT/ha-addon/run.sh" "$OUT/"

echo "Packed add-on at $OUT"
echo "Copy that folder to Home Assistant /addons/cat_printer"
