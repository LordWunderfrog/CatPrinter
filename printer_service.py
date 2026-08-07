"""
Printer job orchestration: lock, probe, wake nudge, one recovery pass.

Transport/protocol live in yhk_printer. HTTP mapping lives in api.
Callers stay dumb — NFC/HA just ask to print or wake.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from yhk_printer import (
    get_config,
    is_busy_error,
    is_retryable_connect_error,
    print_image,
    printer_session,
)

log = logging.getLogger("cat_printer.service")

# RLock: drain holds it for the whole queue pass; print_raster re-enters for each job.
# Probes/wake must not open RFCOMM between jobs or during mech settle.
_print_lock = threading.RLock()

# After bluetoothctl connect, brief pause before RFCOMM (Classic stack settle).
WAKE_BT_SETTLE_S = float(os.environ.get("WAKE_BT_SETTLE_S", "1.0"))
WAKE_BLUETOOTHCTL = os.environ.get("WAKE_BLUETOOTHCTL", "1").strip() not in (
    "0",
    "false",
    "no",
)
# EBUSY after a prior print — wait and retry before treating as unavailable.
PRINT_BUSY_RETRIES = int(os.environ.get("PRINT_BUSY_RETRIES", "5"))
PRINT_BUSY_SETTLE_S = float(os.environ.get("PRINT_BUSY_SETTLE_S", "2.0"))
# One full session retry after a wake nudge (connect() still has its own micro-retries).
# Documented budget: 1 dial with N micro-retries, then at most one wake+redial.


class PrinterError(Exception):
    """Base domain error for printer operations."""


class PrinterUnavailable(PrinterError):
    """Could not open RFCOMM / printer sleepy or unreachable."""


class PrintFailed(PrinterError):
    """Connected but the print job failed."""


@contextmanager
def hold_printer() -> Iterator[None]:
    """Exclusive printer ownership (drain uses this around the whole queue)."""
    with _print_lock:
        yield


def bluetoothctl_nudge(mac: str | None = None) -> str | None:
    """Best-effort Classic reconnect. Returns a short note or None if skipped."""
    if not WAKE_BLUETOOTHCTL:
        return None
    mac = mac or get_config()["mac"]
    try:
        subprocess.run(
            ["bluetoothctl", "disconnect", mac],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        time.sleep(WAKE_BT_SETTLE_S)
        proc = subprocess.run(
            ["bluetoothctl", "connect", mac],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        log.info(
            "event=wake_bluetoothctl mac=%s returncode=%s out=%r",
            mac,
            proc.returncode,
            out[:200],
        )
        return out[:200] if out else f"bluetoothctl exit {proc.returncode}"
    except FileNotFoundError:
        log.info("event=wake_bluetoothctl skipped reason=not_installed")
        return "bluetoothctl not installed"
    except Exception as e:
        log.warning("event=wake_bluetoothctl_fail mac=%s error=%s", mac, e)
        return str(e)


def probe(*, timeout: float) -> dict:
    """
    RFCOMM probe under the print lock.
    Returns ok, printer (awake|busy|sleepy|error), printer_mac, optional detail.
    """
    cfg = get_config()
    if not _print_lock.acquire(blocking=False):
        log.debug("event=probe printer=busy mac=%s", cfg["mac"])
        return {"ok": True, "printer": "busy", "printer_mac": cfg["mac"]}

    try:
        return _probe_unlocked(timeout=timeout)
    finally:
        _print_lock.release()


def _probe_unlocked(*, timeout: float) -> dict:
    """RFCOMM probe; caller must hold `_print_lock`."""
    cfg = get_config()
    try:
        with printer_session(probe=True, timeout=timeout):
            pass
    except OSError as e:
        log.warning("event=probe printer=sleepy mac=%s error=%s", cfg["mac"], e)
        return {
            "ok": False,
            "printer": "sleepy",
            "printer_mac": cfg["mac"],
            "detail": str(e),
        }
    except Exception as e:
        log.warning("event=probe printer=error mac=%s error=%s", cfg["mac"], e)
        return {
            "ok": False,
            "printer": "error",
            "printer_mac": cfg["mac"],
            "detail": str(e),
        }
    log.debug("event=probe printer=awake mac=%s", cfg["mac"])
    return {"ok": True, "printer": "awake", "printer_mac": cfg["mac"]}


def wake(*, probe_timeout: float) -> dict:
    """
    One nudge + one probe. Does not loop — HA owns attempt limits / cooldown.
    Never bluetoothctl-disconnects while a print (or mech settle) holds the lock.
    """
    cfg = get_config()
    mac = cfg["mac"]
    log.info("event=wake_start mac=%s", mac)
    if not _print_lock.acquire(blocking=False):
        log.debug("event=probe printer=busy mac=%s", mac)
        body = {"ok": True, "printer": "busy", "printer_mac": mac}
        log.info("event=wake_ok mac=%s printer=busy", mac)
        return body

    try:
        bt_note = bluetoothctl_nudge(mac)
        body = _probe_unlocked(timeout=probe_timeout)
        if bt_note:
            body = {**body, "bluetoothctl": bt_note}
        if body.get("ok"):
            log.info("event=wake_ok mac=%s printer=%s", mac, body.get("printer"))
        else:
            log.warning("event=wake_fail mac=%s detail=%s", mac, body.get("detail"))
        return body
    finally:
        _print_lock.release()


def run_print(
    job: str,
    req_id: str,
    fn: Callable,
    *,
    settle_s: float = 0.0,
) -> None:
    """
    Determinate print flow under the lock:

      1. Open session (connect micro-retries inside yhk_printer.connect)
      2. Run job
      3. On EBUSY: settle and retry (adapter catching up after prior job)
      4. On sleepy/host-down before any send: one bluetoothctl nudge + one more session
      5. Never wake-retry after bytes may have been sent (would reprint / smash paper)
      6. Hold the lock for settle_s after any send attempt so probes cannot RFCOMM mid-feed

    Raises PrinterUnavailable / PrintFailed. Never raises HTTPException.
    """
    cfg = get_config()
    with _print_lock:
        started = False

        def tracking_fn(soc) -> None:
            nonlocal started
            started = True
            fn(soc)

        try:
            try:
                _run_session_settled(job, req_id, tracking_fn)
            except OSError as first:
                if started:
                    log.error(
                        "event=print_fail req=%s kind=%s error=%s partial_send=1",
                        req_id,
                        job,
                        first,
                    )
                    raise PrintFailed(str(first)) from first
                if is_busy_error(first):
                    log.error(
                        "event=print_fail req=%s kind=%s error=%s", req_id, job, first
                    )
                    raise PrinterUnavailable(str(first)) from first
                if not is_retryable_connect_error(first):
                    log.error(
                        "event=print_fail req=%s kind=%s error=%s", req_id, job, first
                    )
                    raise PrinterUnavailable(str(first)) from first
                log.warning(
                    "event=print_wake_retry req=%s kind=%s error=%s",
                    req_id,
                    job,
                    first,
                )
                bluetoothctl_nudge(cfg["mac"])
                try:
                    _run_session_settled(job, req_id, tracking_fn)
                except OSError as second:
                    log.error(
                        "event=print_fail req=%s kind=%s error=%s after_wake=1",
                        req_id,
                        job,
                        second,
                    )
                    raise PrinterUnavailable(str(second)) from second
                except PrinterError:
                    raise
                except Exception as e:
                    log.error(
                        "event=print_fail req=%s kind=%s error=%s", req_id, job, e
                    )
                    raise PrintFailed(str(e)) from e
            except PrinterError:
                raise
            except Exception as e:
                log.error("event=print_fail req=%s kind=%s error=%s", req_id, job, e)
                raise PrintFailed(str(e)) from e
        finally:
            if settle_s > 0 and started:
                log.debug(
                    "event=print_mech_settle req=%s kind=%s settle_s=%s",
                    req_id,
                    job,
                    round(settle_s, 1),
                )
                time.sleep(settle_s)
    # Success is logged by the spool as event=printed (includes settle/height).


def print_raster(job: str, req_id: str, img, *, settle_s: float = 0.0) -> None:
    """Print a prepared PIL image (mode 1 preferred). Holds lock through settle_s."""
    run_print(job, req_id, lambda soc: print_image(soc, img), settle_s=settle_s)


def _run_session(fn: Callable) -> None:
    with printer_session() as soc:
        fn(soc)


def _run_session_settled(job: str, req_id: str, fn: Callable) -> None:
    """Open a session; on EBUSY wait and retry before surfacing the error."""
    attempts = max(1, PRINT_BUSY_RETRIES)
    last: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            _run_session(fn)
            return
        except OSError as e:
            last = e
            if not is_busy_error(e) or attempt >= attempts:
                raise
            log.warning(
                "event=print_busy_settle req=%s kind=%s attempt=%s/%s error=%s "
                "sleep_s=%s",
                req_id,
                job,
                attempt,
                attempts,
                e,
                PRINT_BUSY_SETTLE_S,
            )
            time.sleep(PRINT_BUSY_SETTLE_S)
    assert last is not None
    raise last
