"""net_guard + reddit image SSRF checks."""
from __future__ import annotations

import pytest

from net_guard import host_is_public
from reddit_image import RedditImageError, _http_get_bytes


def test_host_rejects_loopback():
    assert not host_is_public("127.0.0.1")
    assert not host_is_public("localhost")


def test_host_rejects_private():
    assert not host_is_public("192.168.1.1")
    assert not host_is_public("10.0.0.1")


def test_http_get_bytes_blocks_private(monkeypatch):
    with pytest.raises(RedditImageError, match="Blocked non-public"):
        _http_get_bytes("http://127.0.0.1/secret.png")


def test_http_get_bytes_blocks_bad_scheme():
    with pytest.raises(RedditImageError, match="Unsupported"):
        _http_get_bytes("file:///etc/passwd")
