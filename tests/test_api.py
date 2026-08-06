"""API helpers / validation (no Bluetooth, no TestClient)."""
import pytest
from pydantic import ValidationError

from api import (
    MAX_TEXT_CHARS,
    TextPrintRequest,
    _default_subreddit,
    _extract_token,
    _path_requires_auth,
)


def test_extract_token_from_headers():
    assert _extract_token("Bearer secret", None) == "secret"
    assert _extract_token("bearer secret", None) == "secret"
    assert _extract_token(None, "key-from-header") == "key-from-header"
    assert _extract_token(None, None) is None
    assert _extract_token("Basic nope", None) is None


def test_auth_paths():
    assert _path_requires_auth("/print/reddit")
    assert _path_requires_auth("/printer/wake")
    assert not _path_requires_auth("/status")
    assert not _path_requires_auth("/ready")
    assert not _path_requires_auth("/health")


def test_text_rejects_absurd_size():
    with pytest.raises(ValidationError):
        TextPrintRequest(text="x" * (MAX_TEXT_CHARS + 1))


def test_default_subreddit_env(monkeypatch):
    monkeypatch.setenv("DEFAULT_SUBREDDIT", "aww")
    assert _default_subreddit() == "aww"
    monkeypatch.setenv("DEFAULT_SUBREDDIT", "  ")
    assert _default_subreddit() == "chonkers"
