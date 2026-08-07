#!/usr/bin/env bash
# Assemble a self-contained HA local add-on folder from the single source tree.
# Output: dist/cat_printer/
#   --deploy  also mirrors that folder to the HA /addons share
# Override path with CAT_PRINTER_ADDON_DEPLOY (default //home.lan/addons/cat_printer)
# File list: scripts/addon-files.txt (shared with pack-addon.ps1)
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
MANIFEST="$(dirname "$0")/addon-files.txt"

if [[ ! -f "$MANIFEST" ]]; then
  echo "Missing pack manifest: $MANIFEST" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%$'\r'}"
  line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -z "$line" || "$line" == \#* ]] && continue
  src_rel="${line%%|*}"
  if [[ "$line" == *"|"* ]]; then
    dest_name="${line#*|}"
  else
    dest_name="$(basename "$src_rel")"
  fi
  src="$ROOT/$src_rel"
  if [[ ! -e "$src" ]]; then
    echo "Pack source missing: $src" >&2
    exit 1
  fi
  cp "$src" "$OUT/$dest_name"
done < "$MANIFEST"

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
echo "In HA: Settings → Apps → Cat Printer → Rebuild (then Start if stopped)."
