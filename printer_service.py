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
from typing import Callable

from yhk_printer import (
    get_config,
    is_retryable_connect_error,
    print_image,
    printer_session,
)

log = logging.getLogger("cat_printer.service")

_print_lock = threading.Lock()

# After bluetoothctl connect, brief pause before RFCOMM (Classic stack settle).
WAKE_BT_SETTLE_S = float(os.environ.get("WAKE_BT_SETTLE_S", "1.0"))
WAKE_BLUETOOTHCTL = os.environ.get("WAKE_BLUETOOTHCTL", "1").strip() not in (
    "0",
    "false",
    "no",
)
# One full session retry after a wake nudge (connect() still has its own micro-retries).
# Documented budget: 1 dial with N micro-retries, then at most one wake+redial.


class PrinterError(Exception):
    """Base domain error for printer operations."""


class PrinterUnavailable(PrinterError):
    """Could not open RFCOMM / printer sleepy or unreachable."""


class PrintFailed(PrinterError):
    """Connected but the print job failed."""


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
        log.info("event=probe printer=busy mac=%s", cfg["mac"])
        return {"ok": True, "printer": "busy", "printer_mac": cfg["mac"]}

    try:
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
        log.info("event=probe printer=awake mac=%s", cfg["mac"])
        return {"ok": True, "printer": "awake", "printer_mac": cfg["mac"]}
    finally:
        _print_lock.release()


def wake(*, probe_timeout: float) -> dict:
    """
    One nudge + one probe. Does not loop — HA owns attempt limits / cooldown.
    """
    cfg = get_config()
    mac = cfg["mac"]
    log.info("event=wake_start mac=%s", mac)
    bt_note = bluetoothctl_nudge(mac)
    body = probe(timeout=probe_timeout)
    if bt_note:
        body = {**body, "bluetoothctl": bt_note}
    if body.get("ok"):
        log.info("event=wake_ok mac=%s printer=%s", mac, body.get("printer"))
    else:
        log.warning("event=wake_fail mac=%s detail=%s", mac, body.get("detail"))
    return body


def run_print(
    job: str,
    req_id: str,
    fn: Callable,
) -> None:
    """
    Determinate print flow under the lock:

      1. Open session (connect micro-retries inside yhk_printer.connect)
      2. Run job
      3. On retryable connect failure only: one bluetoothctl nudge + one more session

    Raises PrinterUnavailable / PrintFailed. Never raises HTTPException.
    """
    cfg = get_config()
    with _print_lock:
        try:
            _run_session(fn)
        except OSError as first:
            if not is_retryable_connect_error(first):
                log.error(
                    "event=print_fail job=%s req_id=%s error=%s", job, req_id, first
                )
                raise PrinterUnavailable(str(first)) from first
            log.warning(
                "event=print_wake_retry job=%s req_id=%s error=%s",
                job,
                req_id,
                first,
            )
            bluetoothctl_nudge(cfg["mac"])
            try:
                _run_session(fn)
            except OSError as second:
                log.error(
                    "event=print_fail job=%s req_id=%s error=%s after_wake=1",
                    job,
                    req_id,
                    second,
                )
                raise PrinterUnavailable(str(second)) from second
            except PrinterError:
                raise
            except Exception as e:
                log.error("event=print_fail job=%s req_id=%s error=%s", job, req_id, e)
                raise PrintFailed(str(e)) from e
        except PrinterError:
            raise
        except Exception as e:
            log.error("event=print_fail job=%s req_id=%s error=%s", job, req_id, e)
            raise PrintFailed(str(e)) from e
    log.info("event=print_ok job=%s req_id=%s", job, req_id)


def print_raster(job: str, req_id: str, img) -> None:
    """Print a prepared PIL image (mode 1 preferred)."""
    run_print(job, req_id, lambda soc: print_image(soc, img))


def _run_session(fn: Callable) -> None:
    with printer_session() as soc:
        fn(soc)
