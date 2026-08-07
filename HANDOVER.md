# Cat Printer — agent handover

Fresh-agent briefing. Repo: `LordWunderfrog/CatPrinter` (`C:\Users\AranFroggatt\PythonProjects\CatPrinter`). Branch: `main`.

## What this is

YHK Classic Bluetooth thermal “cat” printer HTTP API for Home Assistant OS. NFC taps → HA package → `POST /print/reddit` → disk spool → RFCOMM print.

Printer MAC: `25:00:27:00:1B:D5`. API on HA host `:8080`. Default subreddit: `wunkus`.

## Current shipped version

**1.1.17** (`ha-addon/config.yaml`). On share after deploy + Rebuild/Update. Confirm in HA: **Settings → Apps → Cat Printer**.

Recent commits:

- (pending) 1.1.17 — Arctic Shift listing (Pullpush fallback); Pullpush alone was 502
- `4196d3e` — rotating app log on `/share/cat_printer/addon.log` (`share:rw`)
- `4fb3b49` — hold print lock for whole drain; no wake-retry reprint after partial send; settle after failed sends; conservative settle (5s floor, 25 px/s)
- `6d4ef50` — settle under print lock (status was RFCOMM mid-feed)
- Earlier: re-list drain until empty; EBUSY = settle-retry not sleepy; HA NFC `mode: queued`

## Architecture (where to look)

| Path | Role |
|------|------|
| `api.py` | FastAPI; print routes spool + 202 |
| `print_spool.py` | Disk spool, drain, mech settle estimate |
| `printer_service.py` | `_print_lock` (RLock), probe/wake, `hold_printer()`, `print_raster` |
| `yhk_printer.py` | RFCOMM / protocol / `estimate_print_height` |
| `reddit_image.py` | Pullpush + image fetch |
| `ha-addon/` | Metadata only (`config.yaml`, `Dockerfile`, `run.sh`) |
| `ha/cat_printer.yaml` | HA package (NFC, REST, revive). Live copy often `S:\packages\` / `\\home.lan\config\packages\` |
| `scripts/pack-addon.ps1` | Pack + `-Deploy` |

App code lives at **repo root**. Do not duplicate app files under `ha-addon/`.

## Deploy

```powershell
powershell -NoProfile -File scripts/pack-addon.ps1 -Deploy
```

Default deploy path: `\\home.lan\addons\cat_printer` (env `CAT_PRINTER_ADDON_DEPLOY`). Samba also exposes the **same** folder as `\\home.lan\local_apps\cat_printer` (`addons` is legacy name). User may map `N:` to `local_apps`.

After deploy: user must **Rebuild** Cat Printer in HA (Settings → **Apps**). Rebuild is not automatic.

Cursor rule: `.cursor/rules/ha-addon-deploy.mdc`. Checkpoint rule: commit/push at significant stops (`.cursor/rules/git-checkpoints.mdc`).

## Logs (important)

Live app log (preferred — no dump step):

`\\home.lan\share\cat_printer\addon.log`  
(or `.ha\share\cat_printer\addon.log` if you linked share — see workspace tip)

Rotating file from the add-on (`LOG_FILE`, map `share:rw`). Agent can read this anytime over Samba.

Add-on stdout is **also** in Supervisor/journal. Optional dump from SSH/Terminal on HA:

```bash
ha apps logs local_cat_printer > /config/cat_printer_addon.log
```

Then read `S:\cat_printer_addon.log` / `\\home.lan\config\cat_printer_addon.log`.

- Slug for CLI: **`local_cat_printer`** (not display name, not bare `cat_printer`).
- Earlier dump showed mostly `/status` awake polls + one Reddit fail: Pullpush **502** for `r/wunkus` — never hit spool.

Optional local layout: gitignored `.ha/` symlinks to UNC shares (see below). `.ha` is already in `.gitignore`.

## Open problem — do not call “done” yet

**Rapid NFC multi-tap smashed / cut off prints** (second job overlapping first on paper). Root causes found and patched in 1.1.13–1.1.15:

1. RFCOMM “done” ≠ mechanical feed done → height-based settle
2. `/status` probed mid-settle (lock released too early)
3. Drain released lock between jobs → probe/ESC@ could abort feed
4. Drain one-shot file list left queued jobs behind

**Not yet validated** on hardware under 1.1.15+ with a successful multi-tap. Last useful log dump had no successful `spool_*` sequence.

### Pass criteria (physical + logs)

Physical: full prints, clear gap, no mid-page cutover.

Logs should show:

- `spool_print_ok … settle_s=… height=…`
- `print_mech_settle … settle_s=…`
- During drain: `probe printer=busy` (not `awake`)
- `spool_drain_done … drained=N stopped=empty depth=0`

If still smashed: lower `SPOOL_PX_PER_SEC` (env, default 25). Do **not** “fix” by dumping extra feed newlines — wastes expensive label stock.

### How to re-test

1. Confirm app version **1.1.16** (rebuild if needed)
2. Printer awake; double/triple NFC tap
3. Read live log: `\\home.lan\share\cat_printer\addon.log` (or `.ha\share\...`)
4. Read logs before burning more labels

## HA package notes

`ha/cat_printer.yaml`: NFC automation `mode: queued`, `max: 10`; status poll `scan_interval: 120`; tag id `fb7b4343-d943-4aa5-ac78-4b640d98bca5`. Secret `cat_printer_api_token` in `secrets.yaml` (can be `""`).

## Don’ts / traps

- Extra `PRINT_FEED_LINES` as a smash “fix” — user rejected; burns plastic label stock
- Treating `print_start` as printer start — it’s HTTP fetch/render; spool order is `spool_enqueue` / `spool_print_ok`
- Deploying without Rebuild
- Committing `dist/`, secrets, `CodeReviewTemp.md` / `Review2.md` (junk)
- Cloud Cursor agent expecting `N:`/`S:` — those are on the home PC. Use **This PC (Remote Control)** with PC awake, or dump logs to `/config`

## Workspace tip for Cursor Agents

Multi-root `.code-workspace` with `N:`/`S:` breaks Agents (cwd becomes `workspace.json`). Prefer **single root** = this repo. Optional:

```powershell
New-Item -ItemType Directory -Force -Path .ha | Out-Null
cmd /c mklink /D ".ha\local_apps" "\\home.lan\local_apps\cat_printer"
cmd /c mklink /D ".ha\config" "\\home.lan\config"
cmd /c mklink /D ".ha\share" "\\home.lan\share"
```

## Tests

```powershell
python -m pytest tests/test_print_spool.py tests/test_printer_service.py tests/test_yhk_printer.py tests/test_api.py -q
```

## User preference

Direct, blunt, no paper waste. Prefer long boring settle gaps over smashed jobs. Don’t agree for comfort — challenge bad approaches.
