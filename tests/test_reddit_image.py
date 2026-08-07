"""Unit tests for reddit_image helpers (no network)."""
from io import BytesIO

import pytest

from reddit_image import (
    RedditImageError,
    _is_direct_image_url,
    _normalize_subreddit,
    _posts_from_listing_children,
    _read_capped,
    list_hot_image_posts,
)


def test_normalize_subreddit():
    assert _normalize_subreddit("chonkers") == "chonkers"
    assert _normalize_subreddit("r/chonkers") == "chonkers"
    assert _normalize_subreddit("/r/Aww/") == "aww"


def test_normalize_rejects_junk():
    with pytest.raises(RedditImageError):
        _normalize_subreddit("../etc")
    with pytest.raises(RedditImageError):
        _normalize_subreddit("")


def test_direct_image_url_rules():
    assert _is_direct_image_url("https://i.redd.it/abc.jpg")
    assert _is_direct_image_url("https://i.imgur.com/x.PNG")
    assert not _is_direct_image_url("https://reddit.com/r/foo")
    assert not _is_direct_image_url("https://i.redd.it/x.gif")


def test_posts_from_pullpush_rows():
    rows = [
        {"title": "a", "url": "https://i.redd.it/a.jpg"},
        {"title": "b", "url": "https://reddit.com/r/x"},
    ]
    posts = _posts_from_listing_children(rows)
    assert len(posts) == 1
    assert posts[0]["url"].endswith(".jpg")


def test_read_capped_ok():
    assert _read_capped(BytesIO(b"chonky"), 100) == b"chonky"


def test_read_capped_rejects_fat_cat():
    with pytest.raises(RedditImageError, match="too large"):
        _read_capped(BytesIO(b"x" * 50), 10)


def test_list_prefers_arctic_over_pullpush(monkeypatch):
    calls: list[str] = []

    def arctic(sub, limit, *, before=None):
        calls.append("arctic")
        return [
            {
                "title": "wunk",
                "url": "https://i.redd.it/x.jpg",
                "permalink": "/r/wunkus/x",
            }
        ]

    def rss(sub, limit):
        calls.append("rss")
        return [{"title": "rss", "url": "https://i.redd.it/r.jpg", "permalink": ""}]

    def pullpush(sub, limit, *, before=None):
        calls.append("pullpush")
        return [{"title": "nope", "url": "https://i.redd.it/y.jpg", "permalink": ""}]

    monkeypatch.setattr("reddit_image._fetch_arctic_shift", arctic)
    monkeypatch.setattr("reddit_image._fetch_reddit_rss", rss)
    monkeypatch.setattr("reddit_image._fetch_pullpush", pullpush)
    monkeypatch.setattr("reddit_image._listing_cache", {})
    posts = list_hot_image_posts("wunkus", limit=10)
    assert posts[0]["title"] == "wunk"
    assert calls == ["arctic"]


def test_list_falls_back_to_rss_then_pullpush(monkeypatch):
    def arctic(sub, limit, *, before=None):
        raise RedditImageError("Arctic Shift listing failed for r/wunkus: boom")

    def rss(sub, limit):
        return [{"title": "rss", "url": "https://i.redd.it/z.png", "permalink": ""}]

    def pullpush(sub, limit, *, before=None):
        return [{"title": "fallback", "url": "https://i.redd.it/p.png", "permalink": ""}]

    monkeypatch.setattr("reddit_image._fetch_arctic_shift", arctic)
    monkeypatch.setattr("reddit_image._fetch_reddit_rss", rss)
    monkeypatch.setattr("reddit_image._fetch_pullpush", pullpush)
    monkeypatch.setattr("reddit_image._listing_cache", {})
    posts = list_hot_image_posts("wunkus")
    assert posts[0]["title"] == "rss"


def test_list_retries_empty_batch_then_succeeds(monkeypatch):
    calls: list[int | None] = []

    def arctic(sub, limit, *, before=None):
        calls.append(before)
        if before is None:
            return []  # recent window all videos
        return [{"title": "older", "url": "https://i.redd.it/o.jpg", "permalink": ""}]

    monkeypatch.setattr("reddit_image._fetch_arctic_shift", arctic)
    monkeypatch.setattr(
        "reddit_image._fetch_reddit_rss",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("skip")),
    )
    monkeypatch.setattr(
        "reddit_image._fetch_pullpush",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("skip")),
    )
    monkeypatch.setattr("reddit_image._listing_cache", {})
    monkeypatch.setattr("reddit_image.LISTING_BATCH_ATTEMPTS", 3)
    posts = list_hot_image_posts("wunkus", limit=20)
    assert posts[0]["title"] == "older"
    assert calls[0] is None
    assert any(c is not None for c in calls[1:])


def test_list_uses_cache(monkeypatch):
    calls = {"n": 0}

    def arctic(sub, limit, *, before=None):
        calls["n"] += 1
        return [{"title": "cached", "url": "https://i.redd.it/c.jpg", "permalink": ""}]

    monkeypatch.setattr("reddit_image._fetch_arctic_shift", arctic)
    monkeypatch.setattr(
        "reddit_image._fetch_reddit_rss",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("skip")),
    )
    monkeypatch.setattr(
        "reddit_image._fetch_pullpush",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("skip")),
    )
    monkeypatch.setattr("reddit_image._listing_cache", {})
    monkeypatch.setattr("reddit_image.LISTING_CACHE_TTL_S", 60.0)
    assert list_hot_image_posts("wunkus")[0]["title"] == "cached"
    assert list_hot_image_posts("wunkus")[0]["title"] == "cached"
    assert calls["n"] == 1


def test_list_both_fail(monkeypatch):
    monkeypatch.setattr("reddit_image._listing_cache", {})
    monkeypatch.setattr("reddit_image.LISTING_BATCH_ATTEMPTS", 2)
    monkeypatch.setattr(
        "reddit_image._fetch_arctic_shift",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("arctic down")),
    )
    monkeypatch.setattr(
        "reddit_image._fetch_reddit_rss",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("rss down")),
    )
    monkeypatch.setattr(
        "reddit_image._fetch_pullpush",
        lambda *a, **k: (_ for _ in ()).throw(RedditImageError("pullpush down")),
    )
    with pytest.raises(RedditImageError, match="No direct image posts"):
        list_hot_image_posts("wunkus")


def test_rss_parses_embedded_images(monkeypatch):
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>drunkus</title>
        <link href="https://www.reddit.com/r/wunkus/comments/x/"/>
        <content type="html">&lt;img src="https://i.redd.it/yjguxx6nvyhh1.jpeg"&gt;</content>
      </entry>
      <entry>
        <title>video</title>
        <link href="https://www.reddit.com/r/wunkus/comments/y/"/>
        <content type="html">https://v.redd.it/abc</content>
      </entry>
    </feed>
    """

    monkeypatch.setattr(
        "reddit_image._http_get_text",
        lambda url, headers, timeout=20.0: atom,
    )
    from reddit_image import _fetch_reddit_rss

    posts = _fetch_reddit_rss("wunkus", 10)
    assert len(posts) == 1
    assert posts[0]["url"].endswith(".jpeg")


def test_clamp_batch_size():
    from reddit_image import _clamp_batch_size

    assert _clamp_batch_size(20) == 20
    assert _clamp_batch_size(100) == 50
    assert _clamp_batch_size(0) == 1
