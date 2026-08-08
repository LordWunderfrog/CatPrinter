# Cat Printer

HTTP API and Home Assistant OS add-on for a **YHK Classic Bluetooth** thermal “cat” printer (RFCOMM, not BLE/GATT).

Typical path: NFC tap → HA package → `POST /print/reddit` → disk spool → settle → RFCOMM print.

| | |
|---|---|
| Repo | `LordWunderfrog/CatPrinter` |
| Shipped add-on version | **1.1.23** (`ha-addon/config.yaml`) |
| Default printer MAC | `25:00:27:00:1B:D5` |
| API | HA host `:8080` |
| Default subreddit | `wunkus` |

---

## Documentation map

| Doc | Audience | Contents |
|-----|----------|----------|
| [AGENTS.md](AGENTS.md) | Cursor / remote agents | Short briefing, traps, where to look |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Dev / agent | Modules, print pipeline, locks, settle |
| [docs/API.md](docs/API.md) | **Callers** | Endpoints, params, status codes, examples (no internals) |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Ops | Pack, Samba paths, Rebuild, HA package |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Ops / debug | Logs, NFC smash checklist, env reference |
| [docs/REDDIT.md](docs/REDDIT.md) | Dev | Listing sources, disk cache, SSRF |
| [docs/MARKDOWN.md](docs/MARKDOWN.md) | Dev | As-built markdown → 384px raster |
| [docs/PARKED.md](docs/PARKED.md) | Future work | **Keep-awake / sleep** — parked with measurements |
| [postman/README.md](postman/README.md) | Manual API | Postman collection pointers |

Upstream heritage (classic BT reverse-engineering notes) lives at the bottom of this file. Behaviour in this fork is defined by the docs above and the code — not by the original walkthrough.

---

## Layout (one copy of the app)

**All application code is at the repo root.** `ha-addon/` is Home Assistant metadata only (`config.yaml`, `Dockerfile`, `run.sh`). Packing copies root modules into `dist/cat_printer/` for the Supervisor.

| Path | Role |
|------|------|
| `api.py` | FastAPI: health/status/wake + `/print/*` → spool **202** |
| `print_spool.py` | Disk spool, drain under lock, inter-job settle |
| `printer_service.py` | `_print_lock`, probe, wake, `print_raster`, busy retries |
| `yhk_printer.py` | RFCOMM session + protocol |
| `reddit_image.py` | Listing + image download |
| `reddit_cache.py` | Per-subreddit still cache (claim/delete) |
| `net_guard.py` | SSRF host checks for outbound images |
| `log_setup.py` | `addon.log` + `probe.log` |
| `image_prep.py` | Photo prep (EXIF, dither) |
| `markdown_renderer.py` | Mistune AST → 384px 1-bit image |
| `cat-printer.py` | CLI smoke test |
| `ha/cat_printer.yaml` | HA package (NFC, status poll, revive) |
| `ha-addon/` | Add-on metadata only |
| `scripts/pack-addon.ps1` | Pack + `-Deploy` |
| `tests/` | pytest |

Do **not** duplicate app files under `ha-addon/`.

---

## Quick start (local)

```powershell
cd C:\Users\AranFroggatt\PythonProjects\CatPrinter
pip install -r requirements.txt
pip install pytest   # not in requirements.txt
python -m pytest tests/ -q --tb=short
python api.py        # http://0.0.0.0:8080 — needs Classic BT for ready/print
```

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/reddit `
  -ContentType 'application/json' -Body (@{ subreddit = 'aww' } | ConvertTo-Json)
```

CLI: `python cat-printer.py --text "hello"` or `python cat-printer.py path\to\image.jpg`.

Print requests **queue and return 202**; physical print happens asynchronously. See [docs/API.md](docs/API.md).

---

## Quick start (Home Assistant)

1. Pack and sync: `powershell -NoProfile -File scripts/pack-addon.ps1 -Deploy`
2. In HA: **Settings → Apps → Cat Printer → Rebuild** (not automatic)
3. Set options (MAC, optional token, default subreddit); USB BT on the HA VM; pair the printer
4. Install package: copy `ha/cat_printer.yaml` → `/config/packages/`, enable `packages: !include_dir_named packages`
5. Secret: `cat_printer_api_token` in `secrets.yaml` (same as add-on `api_token`; `""` for open LAN)

Full paths and Samba share names: [docs/DEPLOY.md](docs/DEPLOY.md).

---

## Mental model

```text
Caller (NFC / curl / Postman)
    → POST /print/*     validate + prepare raster (sync)
    → disk spool        /data/spool  → 202 Accepted
    → drain thread      hold_printer() for whole drain
    → RFCOMM            print_raster + settle gap
    → sleepy?           park job; HA revive or SPOOL_RETRY_S
```

Glanceable success in logs: `event=queued` → `event=printed` → `event=spool_drain_done`.  
HTTP 202 alone is **not** proof of paper out.

---

## Status

**Finished / signed off** (2026-08-08). Hardware acceptance: multi-tap queue, Reddit path (incl. named sub), cache, settle — confirmed on label stock. Final ritual: `POST /print/reddit` → `r/boobs` → boobs on paper.

Shipped add-on **1.1.23**. Only open thread is optional keep-awake work in [docs/PARKED.md](docs/PARKED.md) — revisit only if sleepy becomes painful again.

---

## Heritage (upstream)

This fork builds on classic-Bluetooth YHK cat-printer reverse engineering (WalkPrint / RFCOMM), not BLE/GATT projects. Original RE narrative and pairing notes from the upstream project:

- Broadcast name like `YHK-XXXX` (last four of MAC)
- Pair with `bluetoothctl`, SDP channel (often **2**), RFCOMM bind
- Useful references: [WerWolv blog](https://werwolv.net/blog/cat_printer), [bitbank2/Thermal_Printer](https://github.com/bitbank2/Thermal_Printer), [JJJollyjim/catprinter](https://github.com/JJJollyjim/catprinter), and related Python cat-printer repos

For day-to-day operation of *this* stack, prefer the docs in the table above.
