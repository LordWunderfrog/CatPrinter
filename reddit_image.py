"""
Pick a random direct-image post from a subreddit.

Listing: Arctic Shift first (reliable single-sub archive), Pullpush fallback.
Reddit's own hot.json blocks non-browser clients (403). GIFs skipped (thermal).
Image idea from SubGrabber.html: filter direct image URLs, pick at random,
retry dead CDN links.
"""
from __future__ import annotations

import io
import json
import logging
import random
import re
import urllib.parse
import urllib.request
from typing import Any

import PIL.Image

log = logging.getLogger("cat_printer.reddit")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
IMAGE_URL_RE = re.compile(r"\.(jpe?g|png|webp)(?:\?|$)", re.I)
SKIP_RE = re.compile(r"\.(gif|gifv|mp4|webm)(?:\?|$)", re.I)
ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"
PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"
# Prefix filter: arctic returns mostly videos otherwise; i.redd.it is our sweet spot.
ARCTIC_IMAGE_URL_PREFIX = "https://i.redd.it/"


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


# Crash ceilings — keep in sync with api.MAX_UPLOAD_BYTES default.
DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_LISTING_BYTES = 2 * 1024 * 1024


def _read_capped(resp, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RedditImageError(f"Response too large (max {max_bytes} bytes)")
    return data


def _http_get_text(url: str, headers: dict[str, str], timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _read_capped(resp, DEFAULT_MAX_LISTING_BYTES).decode(
            "utf-8", errors="replace"
        )


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


def _fetch_arctic_shift(sub: str, limit: int) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode(
        {
            "subreddit": sub,
            "limit": limit,
            "url": ARCTIC_IMAGE_URL_PREFIX,
            "sort_type": "created_utc",
            "sort": "desc",
        }
    )
    endpoint = f"{ARCTIC_SHIFT_BASE}?{qs}"
    try:
        text = _http_get_text(
            endpoint,
            _browser_headers(accept="application/json"),
            timeout=30.0,
        )
        data = json.loads(text)
    except Exception as e:
        raise RedditImageError(f"Arctic Shift listing failed for r/{sub}: {e}") from e

    return _posts_from_listing_children(
        _rows_from_listing_payload(data, source="Arctic Shift", sub=sub)
    )


def _fetch_pullpush(sub: str, limit: int) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"subreddit": sub, "size": limit})
    endpoint = f"{PULLPUSH_BASE}?{qs}"
    try:
        text = _http_get_text(
            endpoint,
            _browser_headers(accept="application/json"),
            timeout=30.0,
        )
        data = json.loads(text)
    except Exception as e:
        raise RedditImageError(f"Pullpush listing failed for r/{sub}: {e}") from e

    return _posts_from_listing_children(
        _rows_from_listing_payload(data, source="Pullpush", sub=sub)
    )


def list_hot_image_posts(subreddit: str, limit: int = 100) -> list[dict[str, str]]:
    """Return [{title, url}, ...] with direct image URLs (Arctic Shift, Pullpush fallback)."""
    sub = _normalize_subreddit(subreddit)
    limit = max(1, min(int(limit), 100))
    errors: list[str] = []

    for name, fetch in (
        ("arctic_shift", _fetch_arctic_shift),
        ("pullpush", _fetch_pullpush),
    ):
        try:
            posts = fetch(sub, limit)
        except RedditImageError as e:
            errors.append(str(e))
            log.warning("event=listing_fail source=%s subreddit=%s error=%s", name, sub, e)
            continue
        if posts:
            log.info(
                "event=listing_ok source=%s subreddit=%s posts=%s",
                name,
                sub,
                len(posts),
            )
            return posts
        errors.append(f"{name}: empty image list for r/{sub}")

    detail = "; ".join(errors) if errors else "no sources tried"
    raise RedditImageError(f"No direct image posts found for r/{sub} ({detail})")


def pick_random_image_post(
    subreddit: str = "wunkus",
    limit: int = 100,
) -> dict[str, str]:
    posts = list_hot_image_posts(subreddit, limit=limit)
    return random.choice(posts)


def fetch_random_subreddit_image(
    subreddit: str = "wunkus",
    limit: int = 100,
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
