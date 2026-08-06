"""Disk spool unit tests (no Bluetooth)."""
import json
import time
from pathlib import Path

import PIL.Image
import pytest

from print_spool import PrintSpool, QueueFull


def test_submit_persists_files(tmp_path, monkeypatch):
    printed: list[str] = []

    def fake_print(kind, req_id, img):
        printed.append(kind)

    monkeypatch.setattr("print_spool.print_raster", fake_print)
    spool = PrintSpool(root=tmp_path / "spool", maxsize=4)
    job_id = spool.submit(
        kind="reddit",
        req_id="abc",
        image=PIL.Image.new("1", (8, 8), 1),
        meta={"title": "hi"},
    )
    assert (spool.root / f"{job_id}.png").is_file()
    meta = json.loads((spool.root / f"{job_id}.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "reddit"
    # async drain should clear it
    for _ in range(50):
        if not list(spool.root.glob("*.json")):
            break
        time.sleep(0.05)
    assert printed == ["reddit"]
    assert spool.pending_count() == 0


def test_spool_full(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "print_spool.print_raster",
        lambda *a, **k: (_ for _ in ()).throw(Exception("should not print")),
    )
    # Prevent async drain from deleting while we fill: make drain a no-op
    spool = PrintSpool(root=tmp_path / "spool", maxsize=1)

    def no_drain(*a, **k):
        return None

    monkeypatch.setattr(spool, "drain_async", no_drain)
    spool.submit(kind="a", req_id="1", image=PIL.Image.new("1", (4, 4), 1))
    with pytest.raises(QueueFull):
        spool.submit(kind="b", req_id="2", image=PIL.Image.new("1", (4, 4), 1))


def test_park_on_sleepy_keeps_job(tmp_path, monkeypatch):
    from printer_service import PrinterUnavailable

    monkeypatch.setattr(
        "print_spool.print_raster",
        lambda *a, **k: (_ for _ in ()).throw(PrinterUnavailable("eepy")),
    )
    spool = PrintSpool(root=tmp_path / "spool", maxsize=4)
    monkeypatch.setattr(spool, "drain_async", lambda **k: None)
    job_id = spool.submit(
        kind="reddit",
        req_id="x",
        image=PIL.Image.new("1", (4, 4), 1),
    )
    result = spool.try_drain(reason="test")
    assert result["stopped"] == "sleepy"
    assert result["drained"] == 0
    assert (spool.root / f"{job_id}.png").is_file()
