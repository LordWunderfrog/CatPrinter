# Architecture

## Design principles

1. **Callers stay dumb.** `/print/*` validates, prepares a raster, writes the spool, returns **202**. Physical print is asynchronous.
2. **One RFCOMM owner.** All probe / wake / print / settle that touches the printer goes through `printer_service` and `_print_lock` (re-entrant `RLock`).
3. **Settle under lock.** After a job (success or failed send that may have started feed), hold the lock for the estimated mechanical settle so `/status` cannot open RFCOMM mid-page.
4. **Park, don’t drop, on sleepy.** `PrinterUnavailable` keeps the job on disk; retry after wake or `SPOOL_RETRY_S`.
5. **Don’t burn label stock** to “fix” timing. Prefer boring gaps over extra feed newlines.

## Module responsibilities

| Module | Responsibility |
|--------|----------------|
| `api.py` | FastAPI surface, auth middleware, ceilings, spool submit, drain triggers on ready/status/wake |
| `print_spool.py` | `{id}.png` + `{id}.json` under spool dir; FIFO drain; settle estimate; TTL / fail limit / depth cap |
| `printer_service.py` | Lock, probe, bluetoothctl wake nudge, EBUSY settle-retries, `print_raster`, probe.log + state transitions |
| `yhk_printer.py` | Socket RFCOMM, protocol packets, `print_image`, `create_text_image`, height estimate |
| `reddit_image.py` | Listing waterfall + download; orchestrates cache fill/claim |
| `reddit_cache.py` | Per-sub folder of stills; claim deletes; no cross-sub serving |
| `net_guard.py` | `host_is_public` — block private/loopback/link-local (and redirects onto them) |
| `log_setup.py` | Stdout + rotating job log + separate non-propagating probe log |
| `image_prep.py` | EXIF orient, fit, contrast/sharpen, Floyd–Steinberg → printable L/1 |
| `markdown_renderer.py` | Mistune AST → exact-width mode-`1` image (no FastAPI/BT knowledge) |

Helpers not in the add-on runtime: `_smoke_md.py`, `_dither_compare.py` (local previews).

## End-to-end print pipeline

```text
POST /print/{markdown|image|reddit}
        │
        ├─ validate / fetch / render  (holds request; no print lock yet)
        ├─ write /data/spool/{job}.png + .json
        ├─ log event=queued req=… job=… depth=…
        └─ HTTP 202 { ok, queued, job_id, … }
                │
                ▼
        drain_async (if pending)
                │
                ├─ hold_printer()   ← entire drain, not per-job only
                ├─ for each job (re-list after each so mid-drain enqueues count):
                │     print_raster(…, settle_s=…)
                │     on success: delete job, log event=printed
                │     on PrinterUnavailable: park, arm SPOOL_RETRY_S
                │     on hard fail: increment fail count; drop after QUEUE_PRINT_FAIL_LIMIT
                └─ log event=spool_drain_done
```

### Settle estimate

```text
settle_s = max(SPOOL_INTER_JOB_GAP_S, height_px / SPOOL_PX_PER_SEC)
```

Defaults: **5.0 s** floor, **25 px/s**. If physical smash (mid-page cutover / no gap) returns, **lower** `SPOOL_PX_PER_SEC` — do not add feed newlines.

### Drain triggers

Drain is opportunistic, not a forever poller:

- After enqueue (when drain is armed / printer believed workable)
- After `/ready`, `/status`, `/printer/wake` when probe says `awake` or `busy`
- Once at API startup
- After `SPOOL_RETRY_S` **only while jobs are parked** (sleepy / unavailable)

Empty spool → no RFCOMM polling from the spooler.

### EBUSY vs sleepy

| Condition | Behaviour |
|-----------|-----------|
| Busy / EBUSY-class | Retry under settle (`PRINT_BUSY_RETRIES` × `PRINT_BUSY_SETTLE_S`). Not treated as immediate sleepy. |
| Sleepy / unreachable | `PrinterUnavailable` → park job; HA revive + `/printer/wake`; or retry timer |
| Partial send already started | Do **not** wake-retry reprint (would double-print). Surface failure / settle. |

### Multi-tap

HA NFC automation uses `mode: queued`, `max: 10`. API accepts each request with 202 and grows spool depth. Drain holds the lock across the batch with settle gaps. Validated on hardware (see [OPERATIONS.md](OPERATIONS.md)).

A tap that never hits `/print/reddit` (HA REST/NFC miss) is **not** a queue bug — only `event=queued` counts.

## Data on disk (add-on)

| Path | Purpose | Survives rebuild? |
|------|---------|-------------------|
| `/data/spool/` | Pending print jobs | Yes (`/data`) |
| `/data/reddit_cache/{sub}/` | Cached stills per subreddit | Yes |
| `/share/cat_printer/addon.log` | Job / ops log | Yes (`share:rw`) |
| `/share/cat_printer/probe.log` | Every probe incl. awake | Yes |

Local (no `/data`): spool → repo `.spool/`, cache → `.reddit_cache/`.

## Concurrency model

- FastAPI request threads prepare images concurrently.
- Only one drain holds `hold_printer()` / `_print_lock` for RFCOMM + settle.
- Per-subreddit locks inside `reddit_cache` for claim/store.
- Listing has a small in-memory TTL cache (`REDDIT_LISTING_CACHE_TTL_S`).

## Auth boundary

If `API_TOKEN` is set: `/print/*` and `/printer/wake` require `X-Api-Key` or `Authorization: Bearer`.  
`/health`, `/ready`, `/status` stay open for HA sensors.

## Related

- [API.md](API.md) — route contracts  
- [REDDIT.md](REDDIT.md) — fetch + cache  
- [PARKED.md](PARKED.md) — sleep / keep-awake (not implemented)
