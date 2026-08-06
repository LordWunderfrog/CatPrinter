#!/usr/bin/env bash
# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/
#   --deploy  also mirrors that folder to the HA /addons share
# Override path with CAT_PRINTER_ADDON_DEPLOY (default //home.lan/addons/cat_printer)
set -euo pipefail

DEPLOY=0
DEPLOY_PATH="${CAT_PRINTER_ADDON_DEPLOY:-//home.lan/addons/cat_printer}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy|-Deploy) DEPLOY=1; shift ;;
    --deploy-path) DEPLOY_PATH="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--deploy] [--deploy-path PATH]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/cat_printer"

rm -rf "$OUT"
mkdir -p "$OUT"

cp "$ROOT/yhk_printer.py" "$ROOT/image_prep.py" "$ROOT/reddit_image.py" "$ROOT/api.py" "$ROOT/cat-printer.py" "$ROOT/markdown_renderer.py" "$ROOT/requirements.txt" "$ROOT/Lucon.ttf" "$OUT/"
cp "$ROOT/ha-addon/config.yaml" "$ROOT/ha-addon/Dockerfile" "$ROOT/ha-addon/run.sh" "$OUT/"

echo "Packed add-on at $OUT"

if [[ "$DEPLOY" -eq 0 ]]; then
  echo "Copy that folder to Home Assistant /addons/cat_printer (or re-run with --deploy)"
  exit 0
fi

parent="$(dirname "$DEPLOY_PATH")"
if [[ ! -d "$parent" ]]; then
  echo "Deploy parent not reachable: $parent. Is the Samba share mounted?" >&2
  exit 1
fi

mkdir -p "$DEPLOY_PATH"
rsync -a --delete "$OUT/" "$DEPLOY_PATH/"

echo "Deployed to $DEPLOY_PATH"
echo "In HA: Settings → Add-ons → Cat Printer → Rebuild (then Start if stopped)."
