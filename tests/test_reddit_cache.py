"""Per-subreddit reddit image disk cache."""
from __future__ import annotations

import PIL.Image
import pytest

import reddit_cache as rc
from reddit_cache import RedditImageCache


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "REDDIT_CACHE_ENABLED", True)
    return RedditImageCache(root=tmp_path / "cache")


def test_claim_empty(cache: RedditImageCache):
    assert cache.claim("wunkus") is None


def test_store_claim_deletes(cache: RedditImageCache):
    img = PIL.Image.new("RGB", (16, 16), (1, 2, 3))
    assert cache.store_image(
        "wunkus",
        img,
        {"title": "a", "url": "https://i.redd.it/a.jpg", "permalink": "/r/wunkus/a"},
    )
    assert cache.count("wunkus") == 1
    got = cache.claim("wunkus")
    assert got is not None
    out, post = got
    assert out.size == (16, 16)
    assert post["title"] == "a"
    assert cache.count("wunkus") == 0
    assert cache.claim("wunkus") is None


def test_subs_are_isolated(cache: RedditImageCache):
    img = PIL.Image.new("RGB", (8, 8), (9, 9, 9))
    cache.store_image(
        "wunkus",
        img,
        {"title": "w", "url": "https://i.redd.it/w.jpg", "permalink": ""},
    )
    cache.store_image(
        "chonkers",
        img,
        {"title": "c", "url": "https://i.redd.it/c.jpg", "permalink": ""},
    )
    hit = cache.claim("wunkus")
    assert hit is not None
    assert hit[1]["title"] == "w"
    assert cache.count("chonkers") == 1


def test_novel_sub_creates_folder(cache: RedditImageCache):
    """First touch of an unseen sub must mkdir — no hardcoded sub list."""
    sub = "rarepuppers"
    sub_dir = cache.root / sub
    assert not sub_dir.exists()
    assert cache.claim(sub) is None
    assert sub_dir.is_dir()
    img = PIL.Image.new("RGB", (4, 4), (5, 5, 5))
    assert cache.store_image(
        "RarePuppers",  # case must collapse to same folder
        img,
        {"title": "p", "url": "https://i.redd.it/p.jpg", "permalink": ""},
    )
    assert (cache.root / "rarepuppers").is_dir()
    assert cache.count("RAREPUPPERS") == 1
    hit = cache.claim("rarepuppers")
    assert hit is not None
    assert hit[1]["title"] == "p"


def test_dedupe_same_url(cache: RedditImageCache):
    img = PIL.Image.new("RGB", (4, 4), (0, 0, 0))
    post = {"title": "x", "url": "https://i.redd.it/x.jpg", "permalink": ""}
    assert cache.store_image("wunkus", img, post)
    assert not cache.store_image("wunkus", img, post)
    assert cache.count("wunkus") == 1


def test_fill_then_multiple_claims(cache: RedditImageCache):
    for i in range(3):
        cache.store_image(
            "wunkus",
            PIL.Image.new("RGB", (4, 4), (i, i, i)),
            {
                "title": f"t{i}",
                "url": f"https://i.redd.it/{i}.jpg",
                "permalink": "",
            },
        )
    titles = set()
    for _ in range(3):
        hit = cache.claim("wunkus")
        assert hit is not None
        titles.add(hit[1]["title"])
    assert titles == {"t0", "t1", "t2"}
    assert cache.count("wunkus") == 0


def test_disabled_cache_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "REDDIT_CACHE_ENABLED", False)
    cache = RedditImageCache(root=tmp_path / "cache")
    img = PIL.Image.new("RGB", (4, 4), (1, 1, 1))
    assert not cache.store_image(
        "wunkus", img, {"title": "x", "url": "https://i.redd.it/x.jpg", "permalink": ""}
    )
    assert cache.claim("wunkus") is None
