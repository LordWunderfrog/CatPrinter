# Cat Printer — agent handover

Fresh-agent briefing. Repo: `LordWunderfrog/CatPrinter` (`C:\Users\AranFroggatt\PythonProjects\CatPrinter`). Branch: `main`.

## What this is

YHK Classic Bluetooth thermal “cat” printer HTTP API for Home Assistant OS. NFC taps → HA package → `POST /print/reddit` → disk spool → RFCOMM print.

Printer MAC: `25:00:27:00:1B:D5`. API on HA host `:8080`. Default subreddit: `wunkus`.

## Current shipped version

**1.1.20** (`ha-addon/config.yaml`). On share after deploy + Rebuild/Update. Confirm in HA: **Settings → Apps → Cat Printer**.

Recent commits:

- `5495864` — quieter, non-duplicated logs; `queued`/`printed` correlated by req+job
- `15b5295` — listing batches of 20; retry random `before` window if no stills
- `9a709e8` — Arctic without `url=` (was 422 timeout); RSS fallback; listing cache
- `24927ea` — Arctic Shift listing (Pullpush fallback); Pullpush alone was 502
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

## Queue / settle — validated on hardware

**Multi-tap OK on 1.1.19** (2026-08-07): two NFC taps ~2s apart → depth 2 → sequential drain with settle gaps; no mid-page cutover.

Log excerpt (share `addon.log`):

- `event=queued req=… job=… kind=reddit depth=1` then `depth=2`
- `event=printed req=… job=… settle_s=… height=…`
- `event=spool_drain_done … drained=2 … depth=0`
- Routine `/status` awake polls are silent at INFO

Note: a tap that never reaches `/print/reddit` (HA REST/NFC miss) is not a queue bug — only `event=queued` counts.

### If smash returns

Physical: mid-page cutover or no gap. Then lower `SPOOL_PX_PER_SEC` (env, default 25). Do **not** “fix” with extra feed newlines — wastes label stock.

### Pass criteria (still the checklist)

Physical: full prints, clear gap, no mid-page cutover.

Logs should show:

- `event=queued … depth=…`
- `event=printed … settle_s=… height=…`
- `event=spool_drain_done … drained=N … depth=0`

### How to re-test

1. Confirm app version (Rebuild/Update if needed)
2. Printer awake; double/triple NFC tap
3. Read live log: `\\home.lan\share\cat_printer\addon.log` (or `.ha\share\...`)
4. Read logs before burning more labels

## HA package notes

`ha/cat_printer.yaml`: NFC automation `mode: queued`, `max: 10`; status poll `scan_interval: 120`; tag id `fb7b4343-d943-4aa5-ac78-4b640d98bca5`. Secret `cat_printer_api_token` in `secrets.yaml` (can be `""`).

## Don’ts / traps

- Extra `PRINT_FEED_LINES` as a smash “fix” — user rejected; burns plastic label stock
- Treating `print_start` / HTTP accept as printer start — glanceable order is `event=queued` → `event=printed`
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
python -m pytest tests/ -q --tb=short
```

## User preference

Direct, blunt, no paper waste. Prefer long boring settle gaps over smashed jobs. Don’t agree for comfort — challenge bad approaches.

---

## Backlog for remote agents (ordered)

Out of scope (do not pick up): shopping-list/Grocy integrations, CI pipelines, README rewrite, typed-config/adapter refactors, `.ha` link scripts (user already linked shares), inventing caller payloads.

### 1. Keep-awake / sleep torture — **P0**

**Problem:** Printer goes sleepy; jobs park; HA bounded revive + power button. Queue/settle are fixed; sleep is the remaining reliability hole.

**Do not:** spam full prints, burn label stock, or “fix” sleep with extra feed newlines.

**Phase A — measure (no behaviour change required beyond logging)**

1. From live log `\\home.lan\share\cat_printer\addon.log` (or `.ha\share\...`), characterise:
   - time from last `event=printed` / drain done → first `printer=sleepy` / `spool_park`
   - whether `/status` every 120s already delays sleep or not
   - wake success rate (`wake_ok` vs give-up / needs_button)
2. Add structured logs if gaps exist, e.g. `event=printer_state` transitions, `event=spool_park` with idle_s since last successful print. Keep glanceable (no status spam at INFO).
3. Write findings into this file under a short **Sleep findings** subsection (numbers + log evidence). Bump patch version only if code/log changes ship.

**Phase B — keep-awake attempt (only after A has numbers)**

Design one low-cost strategy, discuss trade-offs in the commit/handover, then implement **behind an env flag** (default off), e.g. `KEEP_AWAKE=0|1`:

Candidates (pick based on A; do not implement all):

- Periodic lightweight RFCOMM probe while spool non-empty or for N minutes after last print
- Less aggressive bluetoothctl disconnect in wake paths
- HA status interval tweak only if A shows 120s polls are useless or harmful

Constraints:

- Must hold `_print_lock` / `hold_printer` rules — never RFCOMM mid-settle/drain
- Must not open sessions that abort mechanical feed
- Paper cost of the strategy must be **zero** (probes only, no raster)

Ship: tests for lock/skip behaviour, version bump, `-Deploy`, tell user to Rebuild, then ask for a soak test.

**Phase C — torture (user runs; agent prepares the checklist)**

Document a soak plan in this file:

- Idle awake 15–30 min with KEEP_AWAKE on vs off; watch sleepy
- Queue a job, power-idle mid-wait, confirm park + revive still sane
- Pass = fewer sleepy events / fewer needs_button without smashed pages

If keep-awake fails: document “accept bounded revive” and stop chasing firmware miracles.

### 2. Remove `/print/text` — **P1**

Markdown + captioned reddit/image cover text needs.

- Delete `POST /print/text`, `TextPrintRequest`, and dead imports in `api.py`
- Keep `yhk_printer.create_text_image` (reddit captions / markdown still use it)
- Update API docstring, Postman docs if they mention `/print/text`, README only if it documents the route
- Tests: remove/adjust any `/print/text` coverage; ensure markdown/reddit still pass
- Version bump + deploy

### 3. Pin dependencies — **P2**

`requirements.txt` is lower-bounds only (`>=`). Rebuilds can pull breaking majors.

- From a known-good venv (or the add-on image deps), pin tested versions (exact `==` or tight `~=` for Pillow, fastapi, uvicorn, starlette/pydantic as needed, mistune, qrcode, python-multipart)
- Do **not** invent a heavy poetry/lock toolchain unless clearly worth it
- Run full `pytest` after pinning
- Version bump only if you also change runtime; otherwise commit pins alone is fine

### 4. Finish SSRF hardening for outbound fetches — **P2**

Markdown already best-effort blocks private hosts (`markdown_renderer`). Audit **all** outbound HTTP(S):

- `reddit_image.py` image download URLs
- Any remaining urlopen/requests in render/fetch paths

Requirements:

- Block link-local, loopback, private RFC1918, metadata IPs (same spirit as markdown)
- Cap redirects onto private nets
- Tests for reject cases (mirror `test_private_host_rejected`)
- Do not break normal `i.redd.it` / public HTTPS stills

LAN API remains trusted for callers; this is defence-in-depth if the API is ever exposed beyond LAN.

---

**Suggested remote-agent order:** 1A → 1B (flagged) → 2 → (3 or 4 when idle). Always read share logs before burning labels. Commit/push at checkpoints; `-Deploy` when HA runtime must change.
