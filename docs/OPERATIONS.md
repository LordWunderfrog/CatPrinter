# Operations

## Logs

| Log | Samba path | What belongs there |
|-----|------------|--------------------|
| Job / ops | `\\home.lan\share\cat_printer\addon.log` | `queued`, `printed`, spool_*, `printer_state` transitions, wake_*, fetch/render failures |
| Probe history | `\\home.lan\share\cat_printer\probe.log` | **Every** probe including awake: `duration_ms`, `idle_s` when known |

Both rotate (~2 MiB × 3 backups) under `share:rw`. Env: `LOG_FILE`, `PROBE_LOG_FILE`.

If `.ha/share` junctions exist: `.ha\share\cat_printer\addon.log`.

Supervisor journal (optional dump on HA):

```bash
ha apps logs local_cat_printer > /config/cat_printer_addon.log
```

Then read `\\home.lan\config\cat_printer_addon.log`.

### Glanceable success chain

```text
event=queued req=… job=… kind=reddit depth=N
event=printed req=… job=… settle_s=… height=…
event=spool_drain_done … drained=N … depth=0
```

Routine successful `/status` / `/health` / `/ready` lines are silenced at INFO so the job log stays readable. Probe detail → `probe.log`.

### Useful events

| Event | Meaning |
|-------|---------|
| `queued` | Accepted onto spool |
| `printed` | Job sent; settle applied |
| `spool_drain_done` | Drain batch finished |
| `spool_park` | Sleepy/unavailable; job kept (`idle_s` when known) |
| `printer_state` | Awake↔sleepy (etc.) transition on main log |
| `reddit_cache_hit` | Served from disk cache |
| `reddit_cache_fill` | Listing downloaded into cache |
| `wake_*` | Wake path activity |

---

## Multi-tap / smash checklist

Validated on hardware (1.1.19+, 2026-08-07): double NFC ~2s apart → depth 2 → sequential drain with settle; no mid-page cutover.

**Pass (physical):** full prints, clear gap between jobs, no mid-page cutover.  
**Pass (logs):** `queued` × N with rising depth → `printed` × N with `settle_s` → `spool_drain_done depth=0`.

### Re-test

1. Confirm Apps page version  
2. Printer awake; double/triple NFC  
3. Read `addon.log` **before** burning more labels  
4. If smash returns: lower `SPOOL_PX_PER_SEC` (default 25). **Do not** add feed newlines / raise `PRINT_FEED_LINES` to “fix” it — burns label stock.

### Misses that are not queue bugs

If there is no `event=queued`, the request never reached the API (NFC/REST miss). Fix HA side first.

---

## Sleepy printer

Printer can nap after idle even with 120s `/status` polls (see [PARKED.md](PARKED.md)). Current mitigation:

1. Jobs **park** on the spool  
2. HA revive automation: up to 3× `POST /printer/wake`  
3. Spool retry timer `SPOOL_RETRY_S` (default 120s) while work remains  
4. Persistent notification if revive gives up — press the cat’s button  

Keep-awake work is **parked**, not unfinished-urgent.

---

## Environment reference

Defaults are what the code uses when the env var is unset. Add-on options only cover the five user-facing knobs; the rest are env-only (Dockerfile / `run.sh` / Supervisor).

### Core

| Variable | Default | Notes |
|----------|---------|--------|
| `PRINTER_MAC` | `25:00:27:00:1B:D5` | |
| `PRINTER_PORT` | `2` | RFCOMM channel |
| `PRINTER_WIDTH` | `384` | |
| `PRINTER_FONT` | `Lucon.ttf` | Add-on: `/app/Lucon.ttf` |
| `PRINTER_CONNECT_RETRIES` | `3` | |
| `PRINTER_CONNECT_RETRY_DELAY` | `1.5` | seconds |
| `API_HOST` | `0.0.0.0` | |
| `API_PORT` | `8080` | |
| `API_TOKEN` | `""` | empty = auth off |
| `DEFAULT_SUBREDDIT` | `wunkus` | |
| `READY_TIMEOUT_S` | `5` | |

### Ceilings

| Variable | Default | Notes |
|----------|---------|--------|
| `MAX_MARKDOWN_CHARS` | `100000` | |
| `MAX_UPLOAD_BYTES` | `15728640` (15 MiB) | |
| `MAX_IMAGE_PIXELS` | `25000000` | |
| `MAX_RENDER_HEIGHT` | `20000` | |
| `MAX_TEXT_CHARS` | `50000` | Defined but unused (no `/print/text`) |

### Spool / settle

| Variable | Default | Notes |
|----------|---------|--------|
| `PRINT_SPOOL_DIR` | `/data/spool` or `.spool` | |
| `MAX_PRINT_QUEUE` | `32` | full → HTTP 503 |
| `SPOOL_TTL_S` | `604800` (7d) | |
| `SPOOL_RETRY_S` | `120` | sleepy retry while parked |
| `SPOOL_INTER_JOB_GAP_S` | `5.0` | settle floor |
| `SPOOL_PX_PER_SEC` | `25` | settle from height |
| `QUEUE_PRINT_FAIL_LIMIT` | `3` | then drop job |

### Printer session

| Variable | Default |
|----------|---------|
| `WAKE_BT_SETTLE_S` | `1.0` |
| `WAKE_BLUETOOTHCTL` | `1` (off: `0`/`false`/`no`) |
| `PRINT_BUSY_RETRIES` | `5` |
| `PRINT_BUSY_SETTLE_S` | `2.0` |
| `PROBE_STEP_DELAY_S` | `0.5` |
| `PRINT_INIT_DELAY_S` | `0.5` |
| `PRINT_START_DELAY_S` | `0.5` |
| `PRINT_DATA_SETTLE_S` | `0.5` |
| `PRINT_END_DELAY_S` | `0.5` |

### Logging / reddit

| Variable | Default |
|----------|---------|
| `LOG_FILE` | add-on: `/share/cat_printer/addon.log` |
| `PROBE_LOG_FILE` | add-on: `/share/cat_printer/probe.log` |
| `REDDIT_CACHE_DIR` | `/data/reddit_cache` or `.reddit_cache` |
| `REDDIT_CACHE_ENABLED` | `1` |
| `REDDIT_LISTING_CACHE_TTL_S` | `300` |
| `REDDIT_LISTING_BATCH_SIZE` | `20` |
| `REDDIT_LISTING_BATCH_ATTEMPTS` | `5` |
| `REDDIT_LISTING_LOOKBACK_DAYS` | `365` |

`KEEP_AWAKE` is **not** implemented — proposed only in [PARKED.md](PARKED.md).

Pack script only: `CAT_PRINTER_ADDON_DEPLOY` → default `\\home.lan\addons\cat_printer`.

---

## Local vs HA debugging

| Goal | Approach |
|------|----------|
| Unit tests | `python -m pytest tests/ -q --tb=short` |
| API without HA | `python api.py` + Postman / Invoke-RestMethod |
| Production behaviour | Pack → Deploy → Rebuild → share logs |
| Cloud agent without Samba drives | Dump `ha apps logs` to `/config`, or use UNC if available |

---

## Don’ts

- Deploy without telling the user to Rebuild  
- Treat HTTP 202 as “printed”  
- Commit `dist/`, secrets, or scratch review markdown  
- Spam full prints / feed lines to keep the printer awake  
- Assume Cloud agents can see `N:` / `S:`
