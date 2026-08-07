# Deploy (Home Assistant OS)

## Mental model

```text
Repo (dev PC)
  → scripts/pack-addon.ps1 [-Deploy]
  → dist/cat_printer/          (local pack; gitignored — do not commit)
  → Samba share                (Supervisor local add-on folder)
  → user Rebuilds in HA UI
  → container runs /app/*.py
```

**Packing is not restarting.** Until Rebuild/Update, HA keeps the old image.

## Samba paths (same folder, two names)

| Path | Notes |
|------|--------|
| `\\home.lan\addons\cat_printer` | Default `CAT_PRINTER_ADDON_DEPLOY` / pack `-Deploy` target. Legacy share name. |
| `\\home.lan\local_apps\cat_printer` | Same directory; current HA “local apps” naming. User may map `N:` here. |

Override deploy target:

```powershell
$env:CAT_PRINTER_ADDON_DEPLOY = '\\home.lan\addons\cat_printer'
# or
powershell -NoProfile -File scripts/pack-addon.ps1 -Deploy -DeployPath '\\home.lan\addons\cat_printer'
```

## Pack contents

Manifest: `scripts/addon-files.txt` (source → pack). Includes app modules, `Lucon.ttf`, `requirements.txt`, and `ha-addon/{config.yaml,Dockerfile,run.sh}` renamed flat into the pack root.

**Do not** put application `.py` files only under `ha-addon/` — they will not be what you think. Root is source of truth; pack copies from root.

## Deploy commands

Windows (preferred on the home PC):

```powershell
cd C:\Users\AranFroggatt\PythonProjects\CatPrinter
# bump ha-addon/config.yaml version when shipping a runtime change
powershell -NoProfile -File scripts/pack-addon.ps1 -Deploy
```

Linux/mac helper: `scripts/pack-addon.sh --deploy`.

Then in Home Assistant:

1. **Settings → Apps → Cat Printer**
2. Refresh if the new version is not listed yet (local store reload can lag)
3. **Rebuild** (then Start if stopped)

Confirm version on that Apps page matches `ha-addon/config.yaml`.

### Supervisor CLI slug

```bash
ha apps logs local_cat_printer
```

Slug is **`local_cat_printer`**, not the display name, not bare `cat_printer`.

## Add-on options (`ha-addon/config.yaml`)

| Option | Default | Env in container |
|--------|---------|------------------|
| `printer_mac` | `25:00:27:00:1B:D5` | `PRINTER_MAC` |
| `printer_port` | `2` | `PRINTER_PORT` |
| `api_port` | `8080` | `API_PORT` |
| `api_token` | `""` | `API_TOKEN` |
| `default_subreddit` | `wunkus` | `DEFAULT_SUBREDDIT` |

`run.sh` also sets `API_HOST=0.0.0.0`, `LOG_FILE=/share/cat_printer/addon.log`, `PROBE_LOG_FILE=/share/cat_printer/probe.log`.

Add-on flags of note: `host_network: true`, `usb: true`, privileged `NET_ADMIN` / `NET_RAW` / `SYS_ADMIN`, `map: share:rw`.

## HA package (NFC + revive)

Source in repo: `ha/cat_printer.yaml`.  
Live copy usually: `\\home.lan\config\packages\cat_printer.yaml` (or `S:\packages\` if mapped).

Install:

1. Enable `homeassistant.packages: !include_dir_named packages` (or equivalent)
2. Copy package YAML into `/config/packages/`
3. In `secrets.yaml`:

```yaml
cat_printer_api_token: ""   # must match add-on api_token
```

4. Reload packages / restart HA as needed
5. NFC: Settings → Tags → put the tag id into the automation trigger (repo default: `fb7b4343-d943-4aa5-ac78-4b640d98bca5`)

Package behaviour summary:

| Piece | Behaviour |
|-------|-----------|
| REST sensor | `GET http://127.0.0.1:8080/status` every **120s** |
| NFC automation | `rest_command.cat_printer_print_reddit`, `mode: queued`, `max: 10` |
| Revive | On sleepy/error (or unavailable), up to **3** wakes; then needs-button notification + **6h** give-up cooldown |
| Reset | When sensor → `awake` / `busy` |

**Sync the package separately** from add-on pack — only when `ha/cat_printer.yaml` changed.

## When to version-bump + deploy

| Change | Bump version? | `-Deploy`? | Rebuild? |
|--------|---------------|------------|----------|
| `api.py`, printer, reddit, markdown, deps, `ha-addon/*` | Yes | Yes | Yes |
| `ha/cat_printer.yaml` only | No | No (copy package) | No (HA reload) |
| Docs, Postman, tests only | No | No | No |
| Local `python api.py` experiments | No | No | No |

Cursor rule: `.cursor/rules/ha-addon-deploy.mdc`.

## Bluetooth bring-up (HA VM)

1. USB Classic BT dongle passed through to the HA OS VM  
2. `bluetoothctl`: scan, pair, trust printer MAC  
3. SDP / RFCOMM channel matches `printer_port` (default **2**)  
4. Start the add-on; `GET /status` should eventually show `awake` when the printer is on  

Wake is best-effort; power-button on the cat is still the final authority (see package give-up notify).

## Reverse proxy

Optional LAN hostname (e.g. Caddy → HA `:8080`). Prefer LAN-only. If `API_TOKEN` is set, put the same secret on clients and in `secrets.yaml`.
