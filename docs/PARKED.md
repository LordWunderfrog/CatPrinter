# Parked work — keep-awake / sleep

**Status:** Parked (2026-08). Queue/settle and Reddit cache are done. Sleep is workable via 120s status polls + HA bounded revive + power button. Revisit only if sleepy becomes painful again.

Do **not** implement Phase B without fresh measurements and an explicit user go-ahead.

---

## Problem

The printer goes **sleepy**; jobs **park** on the spool; HA revive nudges Bluetooth and re-probes. That loop works but is not “always awake.” Keep-awake was the remaining reliability hole — intentionally deferred.

**Do not:**

- Spam full prints to stay awake  
- Burn label stock  
- “Fix” sleep with extra feed newlines / `PRINT_FEED_LINES`

---

## Phase A — measure (partial, keep)

Instrumentation shipped (1.1.21+):

| Signal | Where |
|--------|--------|
| Every probe (incl. awake) | `\\home.lan\share\cat_printer\probe.log` — `duration_ms`, `idle_s` when known |
| State transitions | `addon.log` → `event=printer_state` |
| Park while sleepy | `addon.log` → `event=spool_park … idle_s=…` |

### Sleep findings (2026-08-07)

Evidence from pre-1.1.21 `addon.log` (when awake probes were still INFO on the main log):

| Marker | Time | Note |
|--------|------|------|
| `spool_drain_done` drained=2 | 17:25:00 | last successful print session |
| probe awake | 17:25:19, 17:27:25 | HA `/status` every ~120s still firing |
| probe **sleepy** (timed out) | **17:29:43** | **~4.7 min** after drain done |
| probe awake again | 17:31:57 | ~2.2 min later (HA revive / wake path likely) |
| awake polls continue | 17:31 → 18:10+ | no second sleepy in that window |

Later soak observation (same era of work): printer can also stay awake for long stretches (~hour+) then nap once — behaviour is inconsistent enough that **measure before designing KEEP_AWAKE**.

**Conclusions so far:**

1. Printer can sleep in **under 5 minutes** of idle after prints, even with 120s RFCOMM status polls.  
2. Those polls did **not** prevent the first nap (two awake probes between drain and sleepy).  
3. Auto-return to awake without a logged `wake_*` in the old file is ambiguous — use `probe.log` + `wake_*` under 1.1.21+ for wake success rate.  
4. No `spool_park` in that particular window (no print while sleepy).

**Before Phase B:** Rebuild to current, idle soak, refine idle_s distribution and wake success rate from `probe.log`.

---

## Phase B — keep-awake attempt (not started)

Only after Phase A has more numbers. Design **one** low-cost strategy, discuss trade-offs, implement **behind an env flag default off**, e.g. `KEEP_AWAKE=0|1` (**not present in code today**).

Candidate ideas (pick based on A; do not implement all):

- Periodic lightweight RFCOMM probe while spool non-empty or for N minutes after last print  
- Less aggressive `bluetoothctl` disconnect in wake paths  
- HA status interval tweak only if A shows 120s polls are useless or harmful  

**Constraints:**

- Must respect `_print_lock` / `hold_printer` — never RFCOMM mid-settle/drain  
- Must not open sessions that abort mechanical feed  
- Paper cost must be **zero** (probes only, no raster)  

Ship bar: tests for lock/skip behaviour, version bump, `-Deploy`, user Rebuild, soak test.

---

## Phase C — torture (user runs; agent prepares checklist)

Suggested soak plan when re-opened:

1. Idle awake 15–30 min with KEEP_AWAKE on vs off; watch sleepy in `probe.log`  
2. Queue a job, power-idle mid-wait, confirm park + revive still sane  
3. Pass = fewer sleepy events / fewer `needs_button` without smashed pages  

If keep-awake fails: document “accept bounded revive” and stop chasing firmware miracles.

---

## Related current behaviour (not parked)

These stay as the production mitigation:

- HA `scan_interval: 120` on `/status`  
- Revive: up to 3 wakes, then needs-button + 6h cooldown (`ha/cat_printer.yaml`)  
- Spool park + `SPOOL_RETRY_S` while work remains  

See [OPERATIONS.md](OPERATIONS.md) and [DEPLOY.md](DEPLOY.md).
