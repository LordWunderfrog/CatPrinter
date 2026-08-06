"""
In-memory print queue: accept raster jobs, print when the printer can.

Callers only learn accept / reject. Sleepy printer is the worker's problem.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import PIL.Image

from printer_service import PrintFailed, PrinterUnavailable, print_raster

log = logging.getLogger("cat_printer.queue")

MAX_PRINT_QUEUE = int(os.environ.get("MAX_PRINT_QUEUE", "16"))
# How long to wait between retries when RFCOMM is down (weekend / sleepy cat).
QUEUE_SLEEPY_RETRY_S = float(os.environ.get("QUEUE_SLEEPY_RETRY_S", "30"))
QUEUE_SLEEPY_RETRY_MAX_S = float(os.environ.get("QUEUE_SLEEPY_RETRY_MAX_S", "300"))
# Give up a job after this many non-retryable print failures (not sleepy).
QUEUE_PRINT_FAIL_LIMIT = int(os.environ.get("QUEUE_PRINT_FAIL_LIMIT", "3"))


class QueueFull(Exception):
    """Bounded queue rejected a new job."""


@dataclass
class PrintJob:
    job_id: str
    kind: str
    req_id: str
    image: PIL.Image.Image
    meta: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


class PrintQueue:
    def __init__(self, maxsize: int = MAX_PRINT_QUEUE) -> None:
        self.maxsize = max(1, maxsize)
        self._q: queue.Queue[PrintJob] = queue.Queue(maxsize=self.maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._depth = 0

    @property
    def depth(self) -> int:
        with self._lock:
            return self._depth

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": self.depth,
            "queue_max": self.maxsize,
            "worker_alive": bool(self._thread and self._thread.is_alive()),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name="print-queue",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "event=queue_start max=%s sleepy_retry_s=%s",
            self.maxsize,
            QUEUE_SLEEPY_RETRY_S,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        log.info("event=queue_stop depth=%s", self.depth)

    def submit(
        self,
        *,
        kind: str,
        req_id: str,
        image: PIL.Image.Image,
        meta: dict[str, Any] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:10]
        job = PrintJob(
            job_id=job_id,
            kind=kind,
            req_id=req_id,
            image=image,
            meta=meta or {},
        )
        with self._lock:
            if self._depth >= self.maxsize:
                raise QueueFull(f"Print queue full ({self.maxsize})")
            try:
                self._q.put_nowait(job)
            except queue.Full as e:
                raise QueueFull(f"Print queue full ({self.maxsize})") from e
            self._depth += 1
        log.info(
            "event=queue_enqueue job_id=%s kind=%s req_id=%s depth=%s",
            job_id,
            kind,
            req_id,
            self.depth,
        )
        return job_id

    def _worker(self) -> None:
        backoff = QUEUE_SLEEPY_RETRY_S
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            fail_count = 0
            while not self._stop.is_set():
                try:
                    print_raster(job.kind, job.req_id, job.image)
                    log.info(
                        "event=queue_print_ok job_id=%s kind=%s req_id=%s waited_s=%s",
                        job.job_id,
                        job.kind,
                        job.req_id,
                        int(time.time() - job.enqueued_at),
                    )
                    backoff = QUEUE_SLEEPY_RETRY_S
                    break
                except PrinterUnavailable as e:
                    log.warning(
                        "event=queue_printer_sleepy job_id=%s req_id=%s error=%s "
                        "retry_in_s=%s",
                        job.job_id,
                        job.req_id,
                        e,
                        backoff,
                    )
                    self._stop.wait(backoff)
                    backoff = min(backoff * 1.5, QUEUE_SLEEPY_RETRY_MAX_S)
                except PrintFailed as e:
                    fail_count += 1
                    log.error(
                        "event=queue_print_fail job_id=%s req_id=%s attempt=%s error=%s",
                        job.job_id,
                        job.req_id,
                        fail_count,
                        e,
                    )
                    if fail_count >= QUEUE_PRINT_FAIL_LIMIT:
                        log.error(
                            "event=queue_drop job_id=%s req_id=%s reason=print_failed",
                            job.job_id,
                            job.req_id,
                        )
                        break
                    self._stop.wait(2.0)
                except Exception as e:
                    log.exception(
                        "event=queue_print_crash job_id=%s req_id=%s error=%s",
                        job.job_id,
                        job.req_id,
                        e,
                    )
                    break

            with self._lock:
                self._depth = max(0, self._depth - 1)
            self._q.task_done()


# Process-wide queue (one worker per API process).
print_queue = PrintQueue()
