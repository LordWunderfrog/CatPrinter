# Cat Printer HTTP API — caller guide

How to call the printer from Home Assistant, scripts, Postman, or another service.  
No spool/Bluetooth internals — just the contract.

| | |
|---|---|
| Default base URL (on HA host) | `http://127.0.0.1:8080` |
| LAN / proxy | whatever you pointed at the add-on (e.g. `http://home.lan:8080`) |
| Content type (JSON routes) | `application/json` |

Postman collection: [postman/README.md](../postman/README.md).

---

## Quick rules

1. **Print routes return `202` when the job is accepted**, not when paper is out. Treat `202` + `queued: true` as success for the caller.
2. **Auth is optional.** If the add-on has an `api_token` set, send it on every `/print/*` and on `/printer/wake`. Health/status stay open.
3. **You do not wait for the print.** Fire the request; the printer drains the queue itself. If it’s sleepy, the job stays queued until wake/revive.
4. **There is no `/print/text`.** Use markdown (or reddit/image, which can caption photos).

### Auth headers (when token is set)

```http
X-Api-Key: <token>
```

or

```http
Authorization: Bearer <token>
```

Missing/wrong token → **401**.

---

## Endpoint index

| Method | Path | Auth if token set? | Success | Purpose |
|--------|------|--------------------|---------|---------|
| `GET` | `/health` | No | **200** | Process up; queue depth; config peek |
| `GET` | `/status` | No | **200** | Printer state (`awake` / `busy` / `sleepy` / `error`) |
| `GET` | `/ready` | No | **200** or **503** | Same probe as status; **503** if not usable |
| `POST` | `/printer/wake` | Yes | **200** or **503** | One wake attempt + re-probe |
| `POST` | `/print/reddit` | Yes | **202** | Random still from a subreddit |
| `POST` | `/print/markdown` | Yes | **202** | Render markdown → print |
| `POST` | `/print/image` | Yes | **202** | Upload an image → print |

---

## `GET /health`

Is the API process up? Does **not** talk to the printer.

**Response `200`**

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | Always `true` if you got here |
| `printer_mac` | string | Configured MAC |
| `printer_port` | int | RFCOMM channel |
| `auth_required` | bool | Whether print/wake need a token |
| `default_subreddit` | string | Used when reddit body omits `subreddit` |
| `queue_depth` | int | Jobs waiting / in flight on the spool |
| `queue_max` | int | Cap (full → print routes return **503**) |
| `spool_dir` | string | Server-side path (informational) |

---

## `GET /status`

Ask whether the printer looks reachable. Always **HTTP 200** (good for HA REST sensors).

**Response `200`**

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` if `printer` is `awake` or `busy` |
| `printer` | string | `awake` \| `busy` \| `sleepy` \| `error` |
| `printer_mac` | string | Configured MAC |
| `detail` | string \| null | Extra error text when relevant |

**Caller tip:** poll this if you care about sleepy vs awake. You do **not** need to poll before every print — sleepy jobs are accepted and printed later.

---

## `GET /ready`

Same information as `/status`, different HTTP codes:

| Condition | HTTP |
|-----------|------|
| `awake` or `busy` | **200** |
| `sleepy` / `error` | **503** |

Use this when a script should fail fast if the printer is down. Prefer `/status` for HA sensors.

---

## `POST /printer/wake`

One best-effort wake attempt, then a status probe. **Does not retry in a loop** — if you want multiple attempts, your caller (or HA) loops.

- No body.
- **200** — probe ok after wake  
- **503** — still unreachable  
- **401** — token required and missing/wrong  

Response shape matches `/status`, and may include a `bluetoothctl` field when a BT nudge ran.

---

## `POST /print/reddit`

Print a random direct-image post from a subreddit (title caption + photo).

### Request

JSON body optional.

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|--------|
| `subreddit` | string | No | add-on `default_subreddit` (usually `wunkus`) | `aww`, `r/aww`, `/r/Aww/` all fine |

Empty body, `{}`, or `null` → default subreddit.

```json
{ "subreddit": "boobs" }
```

```json
{}
```

### Success `202`

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` |
| `queued` | bool | `true` |
| `job_id` | string | Spool job id |
| `printed` | string | `"reddit"` |
| `subreddit` | string | Normalized sub used |
| `title` | string | Post title |
| `url` | string | Image URL used |

### Errors

| HTTP | When |
|------|------|
| **401** | Auth required / bad token |
| **413** | Composed image taller than server ceiling |
| **502** | Could not get a printable image from that sub |
| **503** | Queue full |

---

## `POST /print/markdown`

Render a Markdown document and print it (384 px thermal width).

### Request

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `markdown` | string | Yes | 1 … ~100 000 characters |

```json
{
  "markdown": "# Shopping\n\n- milk\n- eggs\n\n```qr\nhttps://example.com\n```"
}
```

Supported flavour (headings, lists, tasks, tables, code, links, images, ` ```qr ` fences, etc.): see [MARKDOWN.md](MARKDOWN.md) if you care about layout quirks. Callers can send normal Markdown and expect a best-effort print.

### Success `202`

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` |
| `queued` | bool | `true` |
| `job_id` | string | Spool job id |
| `printed` | string | `"markdown"` |
| `chars` | int | Length of the markdown string |

### Errors

| HTTP | When |
|------|------|
| **400** / **422** | Missing/invalid body |
| **401** | Auth |
| **413** | Rendered page taller than server ceiling |
| **503** | Queue full |

---

## `POST /print/image`

Upload a photo/image; server preps it for the thermal head and queues a print.

### Request

`multipart/form-data` with one file field:

| Field | Type | Required |
|-------|------|----------|
| `file` | file | Yes |

Typical limits (server-side): ~15 MiB upload, ~25M pixels. Unreadable/empty → **400**; too large → **413**.

### Success `202`

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | bool | `true` |
| `queued` | bool | `true` |
| `job_id` | string | Spool job id |
| `printed` | string | `"image"` |
| `filename` | string | Upload name |
| `bytes` | int | Upload size |
| `size` | `[w, h]` | Pixel size after prep |

### Errors

| HTTP | When |
|------|------|
| **400** | Empty / unreadable image |
| **401** | Auth |
| **413** | Upload or pixel ceiling |
| **503** | Queue full |

---

## What “success” means for callers

| You got… | Means |
|----------|--------|
| **202** + `queued: true` | Job accepted. Your caller is done. |
| **200** on `/status` with `printer: "awake"` | Printer looks ready right now |
| **503** on `/ready` or wake | Not reachable; job may still be accepted on print routes and wait |
| Paper in the tray | Only physical confirmation — not part of the HTTP response |

Do not block your automation waiting for a “print finished” webhook — there isn’t one. If you need depth, poll `GET /health` → `queue_depth`.

---

## Examples

### PowerShell

```powershell
$base = "http://home.lan:8080"
# $h = @{ "X-Api-Key" = "your-token" }   # if auth is on

Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/status"

# Default subreddit
Invoke-RestMethod -Method Post -Uri "$base/print/reddit" `
  -ContentType "application/json" -Body "{}"

# Named subreddit
Invoke-RestMethod -Method Post -Uri "$base/print/reddit" `
  -ContentType "application/json" `
  -Body (@{ subreddit = "aww" } | ConvertTo-Json)

# Markdown
Invoke-RestMethod -Method Post -Uri "$base/print/markdown" `
  -ContentType "application/json" `
  -Body (@{ markdown = "# Hello`n`n- one`n- two" } | ConvertTo-Json)

# Image
curl.exe -X POST "$base/print/image" -F "file=@C:\path\to\photo.jpg"

# Wake once
Invoke-RestMethod -Method Post -Uri "$base/printer/wake"
```

### curl

```bash
curl -s http://home.lan:8080/status

curl -s -X POST http://home.lan:8080/print/reddit \
  -H "Content-Type: application/json" \
  -d '{"subreddit":"chonkers"}'

curl -s -X POST http://home.lan:8080/print/markdown \
  -H "Content-Type: application/json" \
  -d '{"markdown":"# List\n\n- milk\n- eggs"}'

curl -s -X POST http://home.lan:8080/print/image \
  -F "file=@./photo.jpg"

# With token:
curl -s -X POST http://home.lan:8080/print/reddit \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-token" \
  -d '{}'
```

### Home Assistant `rest_command` sketch

```yaml
rest_command:
  cat_printer_print_reddit:
    url: http://127.0.0.1:8080/print/reddit
    method: POST
    content_type: application/json
    headers:
      X-Api-Key: !secret cat_printer_api_token
    payload: '{"subreddit":"wunkus"}'
    timeout: 120
```

(Repo package already ships a default-sub version — see `ha/cat_printer.yaml`.)

---

## Markdown QR tip

Fenced blocks whose info string starts with `qr` or `qrcode` print as a QR code:

````markdown
```qr
https://example.com
```
````
