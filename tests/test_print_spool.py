"""Disk spool unit tests (no Bluetooth)."""
import json
import time

import PIL.Image
import pytest

from print_spool import PrintSpool, QueueFull


def test_submit_persists_files(tmp_path, monkeypatch):
    printed: list[str] = []

    def fake_print(kind, req_id, img, **kwargs):
        printed.append(kind)

    monkeypatch.setattr("print_spool.print_raster", fake_print)
    monkeypatch.setattr("print_spool._mech_settle_s", lambda h: 0)
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
    for _ in range(50):
        if not list(spool.root.glob("*.json")):
            break
        time.sleep(0.05)
    assert printed == ["reddit"]
    assert spool.pending_count() == 0


def test_spool_full(tmp_path, monkeypatch):
    spool = PrintSpool(root=tmp_path / "spool", maxsize=1)
    monkeypatch.setattr(spool, "drain_async", lambda **k: None)
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
    monkeypatch.setattr(spool, "_arm_sleepy_retry", lambda: None)
    job_id = spool.submit(
        kind="reddit",
        req_id="x",
        image=PIL.Image.new("1", (4, 4), 1),
    )
    result = spool.try_drain(reason="test")
    assert result["stopped"] == "sleepy"
    assert result["drained"] == 0
    assert (spool.root / f"{job_id}.png").is_file()


def test_drain_prints_fifo(tmp_path, monkeypatch):
    printed: list[str] = []

    def fake_print(kind, req_id, img, **kwargs):
        printed.append(req_id)

    monkeypatch.setattr("print_spool.print_raster", fake_print)
    monkeypatch.setattr("print_spool._mech_settle_s", lambda h: 0)
    spool = PrintSpool(root=tmp_path / "spool", maxsize=8)
    monkeypatch.setattr(spool, "drain_async", lambda **k: None)
    for i in range(3):
        spool.submit(
            kind="reddit",
            req_id=f"r{i}",
            image=PIL.Image.new("1", (4, 4), 1),
        )
        time.sleep(0.02)  # distinct mtimes
    result = spool.try_drain(reason="test")
    assert result["drained"] == 3
    assert printed == ["r0", "r1", "r2"]
    assert spool.pending_count() == 0


def test_drain_picks_up_mid_drain_enqueue(tmp_path, monkeypatch):
    """Jobs submitted while drain holds the lock must not be left behind."""
    printed: list[str] = []
    spool = PrintSpool(root=tmp_path / "spool", maxsize=8)
    monkeypatch.setattr(spool, "drain_async", lambda **k: None)
    monkeypatch.setattr("print_spool._mech_settle_s", lambda h: 0)

    def fake_print(kind, req_id, img, **kwargs):
        printed.append(req_id)
        if req_id == "first":
            spool.submit(
                kind="reddit",
                req_id="second",
                image=PIL.Image.new("1", (4, 4), 1),
            )

    monkeypatch.setattr("print_spool.print_raster", fake_print)
    spool.submit(
        kind="reddit",
        req_id="first",
        image=PIL.Image.new("1", (4, 4), 1),
    )
    result = spool.try_drain(reason="test")
    assert result["drained"] == 2
    assert printed == ["first", "second"]
    assert spool.pending_count() == 0


def test_mech_settle_scales_with_height(monkeypatch):
    from print_spool import _mech_settle_s

    monkeypatch.setattr("print_spool.SPOOL_INTER_JOB_GAP_S", 5.0)
    monkeypatch.setattr("print_spool.SPOOL_PX_PER_SEC", 25.0)
    assert _mech_settle_s(50) == 5.0
    assert abs(_mech_settle_s(1250) - (1250 / 25.0)) < 1e-6
