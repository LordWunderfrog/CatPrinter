"""
Per-subreddit on-disk cache of downloaded stills.

Layout: {REDDIT_CACHE_DIR}/{subreddit}/{id}.bin + {id}.json
Claim pops one entry (delete from disk). Refill downloads a listing batch into
the sub folder, then claims. Never crosses subreddit directories.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import uuid
from pathlib import Path

import PIL.Image

log = logging.getLogger("cat_printer.reddit_cache")

REDDIT_CACHE_ENABLED = os.environ.get("REDDIT_CACHE_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


def _default_cache_dir() -> Path:
    env = (os.environ.get("REDDIT_CACHE_DIR") or "").strip()
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/reddit_cache")
    return Path(__file__).resolve().parent / ".reddit_cache"


class RedditImageCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or _default_cache_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, sub: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(sub)
            if lock is None:
                lock = threading.Lock()
                self._locks[sub] = lock
            return lock

    def _sub_dir(self, sub: str) -> Path:
        # sub already normalized ([A-Za-z0-9_]+)
        path = self.root / sub.lower()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def count(self, sub: str) -> int:
        return len(list(self._sub_dir(sub).glob("*.json")))

    def known_urls(self, sub: str) -> set[str]:
        urls: set[str] = set()
        for meta_path in self._sub_dir(sub).glob("*.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            url = (data.get("url") or "").strip()
            if url:
                urls.add(url)
        return urls

    def claim(self, sub: str) -> tuple[PIL.Image.Image, dict[str, str]] | None:
        """Atomically take one cached still for this subreddit, or None."""
        if not REDDIT_CACHE_ENABLED:
            return None
        with self._lock_for(sub):
            metas = sorted(self._sub_dir(sub).glob("*.json"), key=lambda p: p.stat().st_mtime)
            if not metas:
                return None
            meta_path = random.choice(metas)
            job_id = meta_path.stem
            img_path = self._image_path(self._sub_dir(sub), job_id)
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                if img_path is None or not img_path.is_file():
                    meta_path.unlink(missing_ok=True)
                    return None
                img = PIL.Image.open(img_path)
                img.load()
                img = img.convert("RGB")
            except Exception as e:
                log.warning(
                    "event=reddit_cache_bad sub=%s id=%s error=%s", sub, job_id, e
                )
                meta_path.unlink(missing_ok=True)
                if img_path is not None:
                    img_path.unlink(missing_ok=True)
                return None

            meta_path.unlink(missing_ok=True)
            img_path.unlink(missing_ok=True)
            remaining = len(list(self._sub_dir(sub).glob("*.json")))
            post = {
                "title": payload.get("title") or "",
                "url": payload.get("url") or "",
                "permalink": payload.get("permalink") or "",
            }
            log.info(
                "event=reddit_cache_hit sub=%s remaining=%s title=%r",
                sub,
                remaining,
                (post["title"] or "")[:48],
            )
            return img, post

    def store_image(
        self,
        sub: str,
        img: PIL.Image.Image,
        post: dict[str, str],
    ) -> bool:
        """Write one still into the subreddit folder. Returns False on skip/fail."""
        if not REDDIT_CACHE_ENABLED:
            return False
        url = (post.get("url") or "").strip()
        with self._lock_for(sub):
            if url and url in self.known_urls(sub):
                return False
            job_id = uuid.uuid4().hex[:12]
            sub_dir = self._sub_dir(sub)
            img_path = sub_dir / f"{job_id}.png"
            meta_path = sub_dir / f"{job_id}.json"
            try:
                img.convert("RGB").save(img_path, format="PNG")
                meta_path.write_text(
                    json.dumps(
                        {
                            "title": post.get("title") or "",
                            "url": url,
                            "permalink": post.get("permalink") or "",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception as e:
                img_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                log.warning(
                    "event=reddit_cache_store_fail sub=%s error=%s", sub, e
                )
                return False
            return True

    def _image_path(self, sub_dir: Path, job_id: str) -> Path | None:
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bin"):
            path = sub_dir / f"{job_id}{ext}"
            if path.is_file():
                return path
        return None


reddit_image_cache = RedditImageCache()
