# Cat Printer — agent handover

Fresh-agent briefing. Repo: `LordWunderfrog/CatPrinter` (`C:\Users\AranFroggatt\PythonProjects\CatPrinter`). Branch: `main`.

## What this is

YHK Classic Bluetooth thermal “cat” printer HTTP API for Home Assistant OS. NFC taps → HA package → `POST /print/reddit` → disk spool → RFCOMM print.

Printer MAC: `25:00:27:00:1B:D5`. API on HA host `:8080`. Default subreddit: `wunkus`.

## Current shipped version

**1.1.23** (`ha-addon/config.yaml`). On share after deploy + Rebuild/Update. Confirm in HA: **Settings → Apps → Cat Printer**.

Recent commits:

- `66e6cab` — per-subreddit reddit image disk cache (claim/delete)
- `1dac511` — probe.log; remove `/print/text`; pin deps; SSRF on image fetches
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

Job log (glanceable — queued/printed only at INFO):

`\\home.lan\share\cat_printer\addon.log`

Probe / awake history (every status poll, including awake — for keep-awake work):

`\\home.lan\share\cat_printer\probe.log`

(or `.ha\share\cat_printer\...` if linked)

Both rotate under `share:rw`. Env: `LOG_FILE`, `PROBE_LOG_FILE`.

Add-on stdout is **also** in Supervisor/journal. Optional dump from SSH/Terminal on HA:

```bash
ha apps logs local_cat_printer > /config/cat_printer_addon.log
```

Then read `S:\cat_printer_addon.log` / `\\home.lan\config\cat_printer_addon.log`.

- Slug for CLI: **`local_cat_printer`** (not display name, not bare `cat_printer`).

Optional local layout: gitignored `.ha/` symlinks to UNC shares (see below). `.ha` is already in `.gitignore`.

## Reddit image cache (1.1.22+)

Per-sub folder under `/data/reddit_cache/{sub}/` (env `REDDIT_CACHE_DIR`). Folders are created on first use for any valid sub name — no allowlist. Flow:

1. Claim cached still for that sub only → delete file → print
2. On miss: list ~20 posts, download usable stills into that sub folder, claim one
3. Next taps hit cache until empty (`event=reddit_cache_hit remaining=N`); never crosses subs

Disable with `REDDIT_CACHE_ENABLED=0`.

## Sleep findings (Phase A — 2026-08-07)

Evidence from pre-1.1.21 `addon.log` (when awake probes were still INFO):

| Marker | Time | Note |
|--------|------|------|
| `spool_drain_done` drained=2 | 17:25:00 | last successful print session |
| probe awake | 17:25:19, 17:27:25 | HA `/status` every ~120s still firing |
| probe **sleepy** (timed out) | **17:29:43** | **~4.7 min** after drain done |
| probe awake again | 17:31:57 | ~2.2 min later (HA revive / wake path likely) |
| awake polls continue | 17:31 → 18:10+ | no second sleepy in that window |

Conclusions so far:

1. Printer can sleep in **under 5 minutes** of idle after prints, even with 120s RFCOMM status polls.
2. Those polls did **not** prevent the first nap (two awake probes between drain and sleepy).
3. Auto-return to awake without a logged `wake_*` in that old file is ambiguous — need `probe.log` + `wake_*` under 1.1.21 for a clean wake success rate.
4. No `spool_park` in that window (no print while sleepy).

**Shipped for further measure (1.1.21):** `\\home.lan\share\cat_printer\probe.log` records every probe (including awake) with `duration_ms` and `idle_s` when known; main log gets `event=printer_state` only on transitions and `event=spool_park … idle_s=…` when a job parks. Re-measure after Rebuild before designing KEEP_AWAKE.

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

## Backlog

- **Keep-awake / sleep torture — parked** — workable with 120s status polls + HA revive for now; revisit if sleepy becomes painful again.
- **Documentation pass** — HANDOVER / README / deploy path naming (`local_apps` vs legacy `addons`) when signing off.

Done recently: probe history log, `/print/text` removed, deps pinned, SSRF on image fetches, **per-subreddit reddit image disk cache**.

## User preference

Direct, blunt, no paper waste. Prefer long boring settle gaps over smashed jobs. Don’t agree for comfort — challenge bad approaches.

---

## Backlog for remote agents (ordered)

Out of scope (do not pick up): shopping-list/Grocy integrations, CI pipelines, README rewrite, typed-config/adapter refactors, `.ha` link scripts (user already linked shares), inventing caller payloads.

### 1. Keep-awake / sleep torture — **P0**

**Problem:** Printer goes sleepy; jobs park; HA bounded revive + power button. Queue/settle are fixed; sleep is the remaining reliability hole.

**Do not:** spam full prints, burn label stock, or “fix” sleep with extra feed newlines.

**Phase A — measure** — in progress / partial:

- Sleep findings subsection above (from 1.1.20-era log).
- `probe.log` + `printer_state` / `spool_park idle_s` shipped in 1.1.21.
- Next: Rebuild, idle soak, refine findings (wake success rate, idle_s distribution) before Phase B.

**Phase B — keep-awake attempt (only after A has more numbers)**

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

### 2. Remove `/print/text` — **done (1.1.21)**

Markdown + captioned reddit/image cover text needs. `create_text_image` kept for captions.

### 3. Pin dependencies — **done (1.1.21)**

`requirements.txt` now uses exact `==` pins from a known-good env.

### 4. Finish SSRF hardening for outbound fetches — **done (1.1.21)**

Shared `net_guard.host_is_public`; markdown + `reddit_image` image downloads block private/loopback and reject redirects onto private nets. Public HTTPS (e.g. `i.redd.it`, imgur) unchanged.

---

**Suggested remote-agent order:** finish 1A soak readings → 1B (flagged) → Phase C checklist. Always read share logs before burning labels. Commit/push at checkpoints; `-Deploy` when HA runtime must change.
