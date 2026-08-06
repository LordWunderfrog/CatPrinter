"""Print queue unit tests (no Bluetooth)."""
import time

import PIL.Image
import pytest

from print_queue import PrintQueue, QueueFull


def test_submit_and_worker_drains(monkeypatch):
    printed: list[str] = []

    def fake_print(kind, req_id, img):
        printed.append(kind)

    monkeypatch.setattr("print_queue.print_raster", fake_print)
    q = PrintQueue(maxsize=4)
    q.start()
    try:
        job_id = q.submit(
            kind="reddit",
            req_id="abc",
            image=PIL.Image.new("1", (8, 8), 1),
        )
        assert job_id
        for _ in range(50):
            if printed:
                break
            time.sleep(0.05)
        assert printed == ["reddit"]
        assert q.depth == 0
    finally:
        q.stop()


def test_queue_full():
    q = PrintQueue(maxsize=1)
    # Don't start worker — fill and block drain
    q.submit(kind="a", req_id="1", image=PIL.Image.new("1", (4, 4), 1))
    with pytest.raises(QueueFull):
        q.submit(kind="b", req_id="2", image=PIL.Image.new("1", (4, 4), 1))
