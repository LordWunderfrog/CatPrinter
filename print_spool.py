"""
Disk print spool: durable pending rasters, opportunistic drain.

Jobs live under PRINT_SPOOL_DIR (default /data/spool on HA, .spool locally).
No forever RFCOMM polling — drain when something already talks to the printer.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import PIL.Image

from printer_service import PrintFailed, PrinterUnavailable, print_raster

log = logging.getLogger("cat_printer.spool")

MAX_PRINT_QUEUE = int(os.environ.get("MAX_PRINT_QUEUE", "32"))
SPOOL_TTL_S = float(os.environ.get("SPOOL_TTL_S", str(7 * 24 * 3600)))
# While jobs are parked (printer sleepy), retry drain on this interval — not when empty.
SPOOL_RETRY_S = float(os.environ.get("SPOOL_RETRY_S", "120"))
# Brief pause between jobs so RFCOMM can release (avoids EBUSY mid-drain).
SPOOL_INTER_JOB_GAP_S = float(os.environ.get("SPOOL_INTER_JOB_GAP_S", "1.5"))
QUEUE_PRINT_FAIL_LIMIT = int(os.environ.get("QUEUE_PRINT_FAIL_LIMIT", "3"))


class QueueFull(Exception):
    """Spool rejected a new job (at capacity)."""


def _default_spool_dir() -> Path:
    env = (os.environ.get("PRINT_SPOOL_DIR") or "").strip()
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/spool")
    return Path(__file__).resolve().parent / ".spool"


class PrintSpool:
    def __init__(
        self,
        root: Path | None = None,
        maxsize: int = MAX_PRINT_QUEUE,
    ) -> None:
        self.root = (root or _default_spool_dir()).resolve()
        self.maxsize = max(1, maxsize)
        self._drain_lock = threading.Lock()
        self._retry_lock = threading.Lock()
        self._retry_pending = False
        self.root.mkdir(parents=True, exist_ok=True)

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": self.pending_count(),
            "queue_max": self.maxsize,
            "spool_dir": str(self.root),
        }

    def pending_count(self) -> int:
        self._expire_old()
        return len(list(self.root.glob("*.json")))

    def submit(
        self,
        *,
        kind: str,
        req_id: str,
        image: PIL.Image.Image,
        meta: dict[str, Any] | None = None,
    ) -> str:
        self._expire_old()
        if self.pending_count() >= self.maxsize:
            raise QueueFull(f"Print spool full ({self.maxsize})")

        job_id = uuid.uuid4().hex[:10]
        payload = {
            "job_id": job_id,
            "kind": kind,
            "req_id": req_id,
            "meta": meta or {},
            "enqueued_at": time.time(),
            "fail_count": 0,
        }
        png_path = self.root / f"{job_id}.png"
        json_path = self.root / f"{job_id}.json"
        tmp_png = self.root / f".{job_id}.png.tmp"
        tmp_json = self.root / f".{job_id}.json.tmp"
        try:
            image.convert("1").save(tmp_png, format="PNG")
            tmp_json.write_text(json.dumps(payload), encoding="utf-8")
            tmp_png.replace(png_path)
            tmp_json.replace(json_path)
        except Exception:
            for p in (tmp_png, tmp_json, png_path, json_path):
                p.unlink(missing_ok=True)
            raise

        log.info(
            "event=spool_enqueue job_id=%s kind=%s req_id=%s depth=%s",
            job_id,
            kind,
            req_id,
            self.pending_count(),
        )
        # Opportunistic: try now if the printer happens to be up (non-blocking).
        self.drain_async(reason="enqueue")
        return job_id

    def drain_async(self, *, reason: str) -> None:
        if self.pending_count() == 0:
            return
        t = threading.Thread(
            target=self.try_drain,
            kwargs={"reason": reason},
            name=f"spool-drain-{reason}",
            daemon=True,
        )
        t.start()

    def try_drain(self, *, reason: str) -> dict[str, Any]:
        """
        Print pending jobs FIFO until empty or printer unavailable.
        Safe to call from wake/status/enqueue; concurrent drains no-op.
        """
        if not self._drain_lock.acquire(blocking=False):
            log.info("event=spool_drain_skip reason=%s detail=already_draining", reason)
            return {"ok": True, "drained": 0, "skipped": True}

        drained = 0
        stopped = None
        try:
            self._expire_old()
            for json_path in self._pending_json_paths():
                job_id = json_path.stem
                png_path = self.root / f"{job_id}.png"
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception as e:
                    log.error("event=spool_bad_meta job_id=%s error=%s", job_id, e)
                    self._drop(job_id)
                    continue
                if not png_path.is_file():
                    log.error("event=spool_missing_png job_id=%s", job_id)
                    self._drop(job_id)
                    continue
                try:
                    img = PIL.Image.open(png_path)
                    img.load()
                    img = img.convert("1")
                except Exception as e:
                    log.error("event=spool_bad_png job_id=%s error=%s", job_id, e)
                    self._drop(job_id)
                    continue

                kind = payload.get("kind") or "spool"
                req_id = payload.get("req_id") or "-"
                try:
                    print_raster(kind, req_id, img)
                except PrinterUnavailable as e:
                    log.info(
                        "event=spool_park reason=%s job_id=%s detail=%s",
                        reason,
                        job_id,
                        e,
                    )
                    stopped = "sleepy"
                    self._arm_sleepy_retry()
                    break
                except PrintFailed as e:
                    fails = int(payload.get("fail_count") or 0) + 1
                    payload["fail_count"] = fails
                    json_path.write_text(json.dumps(payload), encoding="utf-8")
                    log.error(
                        "event=spool_print_fail job_id=%s attempt=%s error=%s",
                        job_id,
                        fails,
                        e,
                    )
                    if fails >= QUEUE_PRINT_FAIL_LIMIT:
                        log.error(
                            "event=spool_drop job_id=%s reason=print_failed", job_id
                        )
                        self._drop(job_id)
                    stopped = "print_failed"
                    break
                except Exception as e:
                    log.exception(
                        "event=spool_print_crash job_id=%s error=%s", job_id, e
                    )
                    stopped = "crash"
                    break

                waited = int(time.time() - float(payload.get("enqueued_at") or time.time()))
                log.info(
                    "event=spool_print_ok job_id=%s kind=%s req_id=%s waited_s=%s "
                    "reason=%s",
                    job_id,
                    kind,
                    req_id,
                    waited,
                    reason,
                )
                self._drop(job_id)
                drained += 1
                if SPOOL_INTER_JOB_GAP_S > 0:
                    time.sleep(SPOOL_INTER_JOB_GAP_S)
        finally:
            self._drain_lock.release()

        log.info(
            "event=spool_drain_done reason=%s drained=%s stopped=%s depth=%s",
            reason,
            drained,
            stopped or "empty",
            self.pending_count(),
        )
        return {
            "ok": True,
            "drained": drained,
            "stopped": stopped,
            "queue_depth": self.pending_count(),
        }

    def _arm_sleepy_retry(self) -> None:
        """One delayed drain while work remains; no polling when the spool is empty."""
        if SPOOL_RETRY_S <= 0:
            return
        with self._retry_lock:
            if self._retry_pending:
                return
            self._retry_pending = True

        def runner() -> None:
            try:
                time.sleep(SPOOL_RETRY_S)
                if self.pending_count() > 0:
                    log.info(
                        "event=spool_retry_wake depth=%s after_s=%s",
                        self.pending_count(),
                        SPOOL_RETRY_S,
                    )
                    self.try_drain(reason="sleepy_retry")
            finally:
                with self._retry_lock:
                    self._retry_pending = False

        threading.Thread(
            target=runner, name="spool-sleepy-retry", daemon=True
        ).start()

    def _pending_json_paths(self) -> list[Path]:
        paths = [p for p in self.root.glob("*.json") if not p.name.startswith(".")]
        paths.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
        return paths

    def _drop(self, job_id: str) -> None:
        for p in (self.root / f"{job_id}.png", self.root / f"{job_id}.json"):
            p.unlink(missing_ok=True)

    def _expire_old(self) -> None:
        if SPOOL_TTL_S <= 0:
            return
        now = time.time()
        for json_path in list(self.root.glob("*.json")):
            if json_path.name.startswith("."):
                continue
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                age = now - float(payload.get("enqueued_at") or now)
            except Exception:
                age = now - json_path.stat().st_mtime
            if age > SPOOL_TTL_S:
                job_id = json_path.stem
                log.warning(
                    "event=spool_expire job_id=%s age_s=%s", job_id, int(age)
                )
                self._drop(job_id)


print_spool = PrintSpool()
