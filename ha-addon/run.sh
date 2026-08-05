#!/usr/bin/env bash
set -euo pipefail

# HA maps add-on options into /data/options.json
if [[ -f /data/options.json ]]; then
  export PRINTER_MAC="$(python -c "import json; print(json.load(open('/data/options.json'))['printer_mac'])")"
  export PRINTER_PORT="$(python -c "import json; print(json.load(open('/data/options.json'))['printer_port'])")"
  export API_PORT="$(python -c "import json; print(json.load(open('/data/options.json'))['api_port'])")"
fi

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8080}"

exec python /app/api.py
