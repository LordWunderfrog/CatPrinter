# HTTP API

Base URL examples:

- Local: `http://localhost:8080`
- HA host: `http://127.0.0.1:8080` (package) or LAN / reverse-proxy hostname

Auth: only when `API_TOKEN` is non-empty. Send `X-Api-Key: <token>` or `Authorization: Bearer <token>` on `/print/*` and `/printer/wake`. Open endpoints never require auth.

---

## `GET /health`

Process up check. **No Bluetooth.**

**200**

```json
{
  "ok": true,
  "printer_mac": "25:00:27:00:1B:D5",
  "printer_port": 2,
  "auth_required": false,
  "default_subreddit": "wunkus",
  "queue_depth": 0,
  "queue_max": 32,
  "spool_dir": "/data/spool"
}
```

---

## `GET /ready`

RFCOMM probe. Timeout: `READY_TIMEOUT_S` (default 5s).

| Result | HTTP |
|--------|------|
| `printer` is `awake` or `busy` (`ok: true`) | **200** |
| sleepy / error | **503** |

Body shape matches `/status` (below). On awake/busy, may trigger `drain_async`.

---

## `GET /status`

Same probe as `/ready`, but **always HTTP 200** (friendly for HA REST sensors).

```json
{
  "ok": true,
  "printer": "awake",
  "printer_mac": "25:00:27:00:1B:D5",
  "detail": null
}
```

`printer` values: `awake` | `busy` | `sleepy` | `error`.

Successful polls are quiet at INFO in `addon.log`; every probe is recorded in `probe.log`.

---

## `POST /printer/wake`

Best-effort wake: optional `bluetoothctl` disconnect/connect (`WAKE_BLUETOOTHCTL`), settle, then RFCOMM probe. **Does not loop** — HA owns attempt limits.

- **200** if probe ok after nudge  
- **503** if still unreachable  
- **401** if token required and missing/wrong  

May include a `bluetoothctl` field in the body when the nudge ran.

---

## `POST /print/markdown`

JSON body:

```json
{ "markdown": "# hello\n\n- milk\n- eggs" }
```

- `markdown`: required, 1 … `MAX_MARKDOWN_CHARS` (default 100_000)
- Renders via `markdown_renderer` then spools

**202**

```json
{
  "ok": true,
  "queued": true,
  "job_id": "…",
  "printed": "markdown",
  "chars": 42
}
```

| Failure | HTTP |
|---------|------|
| empty / validation | **400** / **422** |
| rendered height > `MAX_RENDER_HEIGHT` | **413** |
| spool full | **503** |
| unauthorized | **401** |

---

## `POST /print/image`

`multipart/form-data` field `file` (image bytes).

Ceilings: `MAX_UPLOAD_BYTES` (15 MiB), `MAX_IMAGE_PIXELS` (25M). Prep via `image_prep`.

**202** — includes `filename`, `bytes`, `size` (`[w,h]`), `job_id`, `printed: "image"`.

| Failure | HTTP |
|---------|------|
| empty / unreadable image | **400** |
| too large | **413** |
| spool full | **503** |

---

## `POST /print/reddit`

JSON body optional:

```json
{ "subreddit": "aww" }
```

Omit `subreddit` (or `{}` / null body) → `DEFAULT_SUBREDDIT` (default `wunkus`). Accepts `r/aww` / `/r/Aww/` (normalized lowercase).

Fetches a printable still (cache claim or listing fill — see [REDDIT.md](REDDIT.md)), composes title caption + dithered photo, spools.

**202**

```json
{
  "ok": true,
  "queued": true,
  "job_id": "…",
  "printed": "reddit",
  "subreddit": "aww",
  "title": "…",
  "url": "https://…"
}
```

| Failure | HTTP |
|---------|------|
| listing / download failure (`RedditImageError`) | **502** |
| image too tall after compose | **413** |
| spool full | **503** |

---

## Removed

`POST /print/text` — removed in 1.1.21. Use markdown, or reddit/image captions (`create_text_image` remains for captions).

---

## PowerShell examples

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/status

Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/reddit `
  -ContentType 'application/json' -Body '{}'

Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/reddit `
  -ContentType 'application/json' `
  -Body (@{ subreddit = 'chonkers' } | ConvertTo-Json)

Invoke-RestMethod -Method Post -Uri http://localhost:8080/print/markdown `
  -ContentType 'application/json' `
  -Body (@{ markdown = "# hello`n`n- milk" } | ConvertTo-Json)

# With token:
$h = @{ 'X-Api-Key' = 'your-token' }
Invoke-RestMethod -Method Post -Uri http://localhost:8080/printer/wake -Headers $h
```

Image upload:

```powershell
curl.exe -X POST http://localhost:8080/print/image -F "file=@images/Turtle.jpg"
```

---

## Interpreting success

| Signal | Means |
|--------|--------|
| HTTP **202** | Job accepted onto spool |
| `event=queued` in `addon.log` | Same, with `req` / `job` / `depth` |
| `event=printed` | Raster sent + settle scheduled |
| `event=spool_drain_done` | Drain batch finished |
| Paper in hand | Only physical confirmation |

A tap with no `event=queued` failed before the API (HA/NFC/REST), not in the spooler.
