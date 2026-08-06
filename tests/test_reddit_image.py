"""Unit tests for reddit_image helpers (no network)."""
from io import BytesIO

import pytest

from reddit_image import (
    RedditImageError,
    _is_direct_image_url,
    _normalize_subreddit,
    _posts_from_listing_children,
    _read_capped,
)


def test_normalize_subreddit():
    assert _normalize_subreddit("chonkers") == "chonkers"
    assert _normalize_subreddit("r/chonkers") == "chonkers"
    assert _normalize_subreddit("/r/Aww/") == "Aww"


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
