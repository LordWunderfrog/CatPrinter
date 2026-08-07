"""
Pick a random direct-image post from a subreddit.

Listing order: Arctic Shift → Reddit RSS → Pullpush.
Small batches (default 20). If a batch has no usable stills, draw another
random time window and try again — do not pull 100-post dumps.
Reddit hot.json is 403 for non-browser clients. Arctic's `url=` filter times
out (422); filter direct images client-side. GIFs skipped (thermal).
"""
from __future__ import annotations

import io
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

import PIL.Image

log = logging.getLogger("cat_printer.reddit")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
IMAGE_URL_RE = re.compile(r"\.(jpe?g|png|webp)(?:\?|$)", re.I)
SKIP_RE = re.compile(r"\.(gif|gifv|mp4|webm)(?:\?|$)", re.I)
# Absolute image URLs embedded in Atom content (RSS fallback).
EMBEDDED_IMAGE_RE = re.compile(
    r"https://(?:i\.redd\.it|i\.imgur\.com)/[^\s\"'<>]+?\.(?:jpe?g|png|webp)(?:\?[^\s\"'<>]*)?",
    re.I,
)
ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"
PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Crash ceilings — keep in sync with api.MAX_UPLOAD_BYTES default.
DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_LISTING_BYTES = 2 * 1024 * 1024
LISTING_CACHE_TTL_S = float(os.environ.get("REDDIT_LISTING_CACHE_TTL_S", "300"))
LISTING_BATCH_SIZE = int(os.environ.get("REDDIT_LISTING_BATCH_SIZE", "20"))
LISTING_BATCH_ATTEMPTS = int(os.environ.get("REDDIT_LISTING_BATCH_ATTEMPTS", "5"))
LISTING_LOOKBACK_DAYS = int(os.environ.get("REDDIT_LISTING_LOOKBACK_DAYS", "365"))

_listing_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


class RedditImageError(Exception):
    """Could not obtain a printable image from a subreddit listing."""


def _browser_headers(*, accept: str, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _read_capped(resp, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RedditImageError(f"Response too large (max {max_bytes} bytes)")
    return data


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(300).decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    if body:
        return f"HTTP Error {exc.code}: {exc.reason} ({body})"
    return f"HTTP Error {exc.code}: {exc.reason}"


def _http_get_text(url: str, headers: dict[str, str], timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _read_capped(resp, DEFAULT_MAX_LISTING_BYTES).decode(
                "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as e:
        raise RedditImageError(_http_error_detail(e)) from e


def _http_get_bytes(
    url: str,
    timeout: float = 30.0,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> bytes:
    req = urllib.request.Request(
        url,
        headers=_browser_headers(
            accept="image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            referer="https://www.reddit.com/",
        ),
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _read_capped(resp, max_bytes)


def _normalize_subreddit(name: str) -> str:
    # Accept "wunkus", "r/wunkus", "/r/Aww/"
    name = (name or "").strip().strip("/")
    if name.lower().startswith("r/"):
        name = name[2:].strip("/")
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RedditImageError(f"Invalid subreddit name: {name!r}")
    return name


def _is_direct_image_url(url: str) -> bool:
    if not url or SKIP_RE.search(url):
        return False
    return bool(IMAGE_URL_RE.search(url))


def _posts_from_listing_children(children: list[Any]) -> list[dict[str, str]]:
    posts: list[dict[str, str]] = []
    for child in children:
        post = (child or {}).get("data") or child or {}
        if not isinstance(post, dict):
            continue
        url = (post.get("url") or post.get("url_overridden_by_dest") or "").strip()
        if _is_direct_image_url(url):
            posts.append(
                {
                    "title": post.get("title") or "",
                    "url": url,
                    "permalink": post.get("permalink") or "",
                }
            )
    return posts


def _rows_from_listing_payload(data: Any, *, source: str, sub: str) -> list[Any]:
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RedditImageError(f"Unexpected {source} payload for r/{sub}")
    return rows


def _clamp_batch_size(limit: int | None) -> int:
    raw = LISTING_BATCH_SIZE if limit is None else int(limit)
    return max(1, min(raw, 50))


def _random_before_utc() -> int:
    """Unix seconds somewhere in the lookback window (exclusive of 'now')."""
    now = int(time.time())
    window = max(86400, LISTING_LOOKBACK_DAYS * 86400)
    return now - random.randint(3600, window)


def _fetch_arctic_shift(
    sub: str,
    limit: int,
    *,
    before: int | None = None,
) -> list[dict[str, str]]:
    # Do NOT pass url= — Arctic returns 422 "Timeout. Maybe slow down a bit".
    params: dict[str, Any] = {
        "subreddit": sub,
        "limit": limit,
        "sort_type": "created_utc",
        "sort": "desc",
    }
    if before is not None:
        params["before"] = before
    endpoint = f"{ARCTIC_SHIFT_BASE}?{urllib.parse.urlencode(params)}"
    try:
        text = _http_get_text(
            endpoint,
            _browser_headers(accept="application/json"),
            timeout=30.0,
        )
        data = json.loads(text)
    except RedditImageError:
        raise
    except Exception as e:
        raise RedditImageError(f"Arctic Shift listing failed for r/{sub}: {e}") from e

    return _posts_from_listing_children(
        _rows_from_listing_payload(data, source="Arctic Shift", sub=sub)
    )


def _fetch_reddit_rss(sub: str, limit: int) -> list[dict[str, str]]:
    endpoint = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}.rss"
    try:
        text = _http_get_text(
            endpoint,
            _browser_headers(
                accept="application/atom+xml,application/xml,text/xml,*/*;q=0.8",
                referer="https://www.reddit.com/",
            ),
            timeout=20.0,
        )
        root = ET.fromstring(text)
    except RedditImageError:
        raise
    except Exception as e:
        raise RedditImageError(f"Reddit RSS listing failed for r/{sub}: {e}") from e

    posts: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in root.findall("a:entry", ATOM_NS):
        title = entry.findtext("a:title", default="", namespaces=ATOM_NS) or ""
        link_el = entry.find("a:link", ATOM_NS)
        permalink = (link_el.get("href") if link_el is not None else "") or ""
        content = entry.findtext("a:content", default="", namespaces=ATOM_NS) or ""
        for url in EMBEDDED_IMAGE_RE.findall(content):
            if url in seen or not _is_direct_image_url(url):
                continue
            seen.add(url)
            posts.append({"title": title, "url": url, "permalink": permalink})
            if len(posts) >= limit:
                return posts
    return posts


def _fetch_pullpush(
    sub: str,
    limit: int,
    *,
    before: int | None = None,
) -> list[dict[str, str]]:
    params: dict[str, Any] = {"subreddit": sub, "size": limit}
    if before is not None:
        params["before"] = before
    endpoint = f"{PULLPUSH_BASE}?{urllib.parse.urlencode(params)}"
    try:
        text = _http_get_text(
            endpoint,
            _browser_headers(accept="application/json"),
            timeout=30.0,
        )
        data = json.loads(text)
    except RedditImageError:
        raise
    except Exception as e:
        raise RedditImageError(f"Pullpush listing failed for r/{sub}: {e}") from e

    return _posts_from_listing_children(
        _rows_from_listing_payload(data, source="Pullpush", sub=sub)
    )


def _cache_get(sub: str) -> list[dict[str, str]] | None:
    hit = _listing_cache.get(sub)
    if not hit:
        return None
    expires_at, posts = hit
    if time.monotonic() >= expires_at or not posts:
        return None
    return list(posts)


def _cache_put(sub: str, posts: list[dict[str, str]]) -> None:
    if LISTING_CACHE_TTL_S <= 0 or not posts:
        return
    _listing_cache[sub] = (time.monotonic() + LISTING_CACHE_TTL_S, list(posts))


def _batches_with_images(
    name: str,
    sub: str,
    batch_size: int,
    fetch: Callable[..., list[dict[str, str]]],
    *,
    supports_before: bool,
) -> list[dict[str, str]]:
    """
    Pull small batches until at least one direct image appears.
    First try = newest; later tries = random `before` window when supported.
    """
    attempts = max(1, LISTING_BATCH_ATTEMPTS if supports_before else 1)
    last_err: RedditImageError | None = None
    for attempt in range(attempts):
        before = None if attempt == 0 or not supports_before else _random_before_utc()
        try:
            if supports_before:
                posts = fetch(sub, batch_size, before=before)
            else:
                posts = fetch(sub, batch_size)
        except RedditImageError as e:
            last_err = e
            log.warning(
                "event=listing_batch_fail source=%s subreddit=%s attempt=%s before=%s error=%s",
                name,
                sub,
                attempt + 1,
                before,
                e,
            )
            continue
        if posts:
            log.debug(
                "event=listing_ok source=%s subreddit=%s posts=%s attempt=%s before=%s",
                name,
                sub,
                len(posts),
                attempt + 1,
                before,
            )
            return posts
        log.debug(
            "event=listing_batch_empty source=%s subreddit=%s attempt=%s before=%s",
            name,
            sub,
            attempt + 1,
            before,
        )
    if last_err is not None:
        raise last_err
    return []


def list_hot_image_posts(
    subreddit: str,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Return [{title, url}, ...] with direct image URLs (small batched fetches)."""
    sub = _normalize_subreddit(subreddit)
    batch_size = _clamp_batch_size(limit)

    cached = _cache_get(sub)
    if cached:
        log.debug(
            "event=listing_ok source=cache subreddit=%s posts=%s",
            sub,
            len(cached),
        )
        return cached[:batch_size]

    errors: list[str] = []
    sources: list[tuple[str, Callable[..., list[dict[str, str]]], bool]] = [
        ("arctic_shift", _fetch_arctic_shift, True),
        ("reddit_rss", _fetch_reddit_rss, False),
        ("pullpush", _fetch_pullpush, True),
    ]

    for name, fetch, supports_before in sources:
        try:
            posts = _batches_with_images(
                name,
                sub,
                batch_size,
                fetch,
                supports_before=supports_before,
            )
        except RedditImageError as e:
            errors.append(str(e))
            log.warning(
                "event=listing_fail source=%s subreddit=%s error=%s", name, sub, e
            )
            continue
        if posts:
            _cache_put(sub, posts)
            return posts
        errors.append(f"{name}: no usable images in {LISTING_BATCH_ATTEMPTS} batch(es)")

    detail = "; ".join(errors) if errors else "no sources tried"
    raise RedditImageError(f"No direct image posts found for r/{sub} ({detail})")


def pick_random_image_post(
    subreddit: str = "wunkus",
    limit: int | None = None,
) -> dict[str, str]:
    posts = list_hot_image_posts(subreddit, limit=limit)
    return random.choice(posts)


def fetch_random_subreddit_image(
    subreddit: str = "wunkus",
    limit: int | None = None,
    attempts: int = 8,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_pixels: int | None = None,
) -> tuple[PIL.Image.Image, dict[str, str]]:
    """
    Pick and download a random image. Retries on dead CDN links.
    Returns (PIL image, post metadata).
    """
    posts = list_hot_image_posts(subreddit, limit=limit)
    random.shuffle(posts)
    last_err: Exception | None = None
    for post in posts[: max(1, attempts)]:
        try:
            raw = _http_get_bytes(post["url"], max_bytes=max_bytes)
            img = PIL.Image.open(io.BytesIO(raw))
            img.load()
            w, h = img.size
            if max_pixels is not None and w * h > max_pixels:
                raise RedditImageError(
                    f"Image too large ({w}x{h}; max {max_pixels} pixels)"
                )
            return img.convert("RGB"), post
        except Exception as e:
            last_err = e
            continue
    raise RedditImageError(
        f"Could not download an image from r/{_normalize_subreddit(subreddit)}"
        + (f": {last_err}" if last_err else "")
    )
