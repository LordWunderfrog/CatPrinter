#!/usr/bin/env bash
set -euo pipefail

# HA maps add-on options into /data/options.json
if [[ -f /data/options.json ]]; then
  eval "$(python - <<'PY'
import json, shlex
opts = json.load(open("/data/options.json"))
mapping = {
    "PRINTER_MAC": "printer_mac",
    "PRINTER_PORT": "printer_port",
    "API_PORT": "api_port",
    "API_TOKEN": "api_token",
    "DEFAULT_SUBREDDIT": "default_subreddit",
}
for env, key in mapping.items():
    if key in opts and opts[key] is not None:
        print(f"export {env}={shlex.quote(str(opts[key]))}")
PY
)"
fi

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8080}"
export DEFAULT_SUBREDDIT="${DEFAULT_SUBREDDIT:-wunkus}"
# Samba-visible via \\home.lan\share\cat_printer\
export LOG_FILE="${LOG_FILE:-/share/cat_printer/addon.log}"
export PROBE_LOG_FILE="${PROBE_LOG_FILE:-/share/cat_printer/probe.log}"
mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PROBE_LOG_FILE")" || true

exec python /app/api.py
