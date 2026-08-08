# Reddit images

Endpoint: `POST /print/reddit` — see [API.md](API.md).  
Orchestration: `reddit_image.py`. Disk cache: `reddit_cache.py`. SSRF: `net_guard.py`.

## Goal

Return one printable still (plus title caption) for a subreddit, with:

- Small listing batches (default **20**)
- Cheap subsequent prints via **per-subreddit disk cache**
- No serving another sub’s images
- No SSRF into private networks

## Listing waterfall

`list_hot_image_posts(sub)` tries, in order:

1. **Arctic Shift** — primary. Do **not** pass a server-side `url=` filter (historically caused **422** / timeouts). Filter to direct image URLs client-side.
2. **Reddit Atom RSS** — fallback when Arctic fails/empty.
3. **Pullpush** — last resort (has been **502** in the wild).

In-memory listing cache: `REDDIT_LISTING_CACHE_TTL_S` (default **300** s) keyed by normalized sub name.

### Batching

| Knob | Default |
|------|---------|
| `REDDIT_LISTING_BATCH_SIZE` | `20` (clamped 1–50) |
| `REDDIT_LISTING_BATCH_ATTEMPTS` | `5` |
| `REDDIT_LISTING_LOOKBACK_DAYS` | `365` |

First attempt = newest window. Later attempts may use a random `before` timestamp when the source supports it, until at least one direct still appears.

### What counts as a still

Direct image URLs (e.g. `i.redd.it`, common image hosts). **GIFs / video / galleries-as-video are skipped.** Sub name must match `[A-Za-z0-9_]+` after stripping `r/` — stored **lowercase**.

## Disk cache (fill → claim → delete)

Layout:

```text
{REDDIT_CACHE_DIR}/{subreddit}/{id}.png
{REDDIT_CACHE_DIR}/{subreddit}/{id}.json   # title, url, permalink
```

Add-on default: `/share/cat_printer/reddit_cache` (Samba: `\\home.lan\share\cat_printer\reddit_cache`).  
Fallback: `/data/reddit_cache` if `/share` is missing; locally `.reddit_cache`.  
Disable: `REDDIT_CACHE_ENABLED=0`.

### Flow (`fetch_random_subreddit_image`)

```text
1. Claim one cached still for this sub only (random among metas)
      → delete .png + .json on successful claim
      → log event=reddit_cache_hit remaining=N
2. On miss / empty:
      → list ~20 posts
      → download each usable still into the sub folder (dedupe by url)
      → log event=reddit_cache_fill listed=… stored=… depth=…
      → claim one (delete) for the current print
3. Next N-1 requests for the same sub are cache hits until empty
```

**Any valid sub name** gets a folder on first use (`mkdir`). There is **no allowlist** — `wunkus` / `chonkers` are examples, not a closed set. Case folds to one directory (`RarePuppers` → `rarepuppers`).

Subs never cross: claiming `aww` will not pop from `wunkus/`.

### Why this shape

Simplest reliable path: fill the folder from one listing, print-and-delete one file per request. Timing of “print first vs store first” is internal; behaviourally the print always comes from a claim after a successful store batch (or from a prior fill).

## SSRF

Image downloads (reddit + markdown embeds) use `net_guard.host_is_public`:

- Block private, loopback, link-local, etc.
- Reject redirects that land on non-public hosts
- Public HTTPS (e.g. `i.redd.it`, imgur) allowed

## Log lines to expect

```text
event=listing_ok source=… subreddit=aww posts=…
event=reddit_cache_fill sub=aww listed=20 stored=7 depth=7
event=reddit_cache_hit sub=aww remaining=6 title='…'
event=queued … kind=reddit …
```

## Failure modes

| Symptom | Likely cause |
|---------|----------------|
| HTTP **502** on `/print/reddit` | All listing sources failed, or zero usable downloads |
| Always refill every tap | Cache disabled, wrong sub name each time, or cache dir not writable |
| Private-IP URL rejected | SSRF guard working as designed |
| Empty after fill `stored=0` | Listing had no stills (retries / `before` window); or downloads failed |

## Related env

See [OPERATIONS.md](OPERATIONS.md) — `REDDIT_*` table.
