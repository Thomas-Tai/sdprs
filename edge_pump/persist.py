# -*- coding: utf-8 -*-
"""NVS-backed counters that must survive a brownout (spec §5.6, §5.8).

WHY NVS AND NOT RTC MEMORY: RTC memory survives soft reset and deep sleep
but is CLEARED by power-on and brownout reset. Brownout is the dominant
reset cause during a typhoon — unstable mains, pump inrush dragging the
rail down. An RTC-backed reset-loop counter reads zero exactly when the
loop it detects is happening, and fails toward NO protection. NVS (flash)
survives both.

FLASH ENDURANCE: every write here costs part of a finite erase budget
(~10^5 cycles). Callers write at boot, on a confirmed-healthy uptime, and
on OFF->ON contactor transitions — NEVER per control-loop iteration.

Every function tolerates `nvs=None` so a node with unavailable NVS still
runs with degraded protection rather than boot-looping on its own loop
detector. `machine`/`esp32` are imported lazily, so this stays
desktop-importable.
"""

BOOT_COUNT_KEY = "boot_count"
CONTACTOR_OPS_KEY = "contactor_ops"
_NAMESPACE = "sdprs"


def open_nvs():
    """Return an esp32.NVS handle, or None when unavailable (desktop, or a
    device whose flash partition is missing)."""
    try:
        from esp32 import NVS
        return NVS(_NAMESPACE)
    except Exception:
        return None


def _read(nvs, key):
    if nvs is None:
        return 0
    try:
        return nvs.get_i32(key)
    except OSError:
        return 0          # key never written — normal on first boot
    except Exception:
        return 0


def _write(nvs, key, value):
    if nvs is None:
        return False
    try:
        nvs.set_i32(key, value)
        nvs.commit()
        return True
    except Exception:
        return False      # worn flash must not take the node down


def read_boot_count(nvs):
    return _read(nvs, BOOT_COUNT_KEY)


def register_boot(nvs):
    """Increment the persisted boot counter. Call EXACTLY ONCE per boot,
    and only on a profile that actually uses it (boot_loop_threshold > 0) —
    every call costs a flash erase cycle.

    Returns the new count, or 0 if it could not be persisted.

    BE CLEAR ABOUT WHAT A 0 MEANS: it makes is_reset_loop() False, so the
    node falls back to the NORMAL hold-off instead of the longer reset-loop
    one. That is the UNSAFE direction — the detector reads "no anomaly"
    when it simply cannot see. It is the same failure direction that got
    RTC memory rejected in spec finding B2, reached by a different route
    (unavailable or worn flash rather than a brownout clearing the store).

    It is tolerated because the alternative — refusing to run without NVS —
    would let a dead flash partition stop a pump during a flood, and that
    is worse. It is NOT tolerated silently: publish `nvs_ok` so "counter is
    zero" and "counter is unreadable" are distinguishable at the dashboard
    (Task 11), because from the telemetry alone they otherwise look
    identical.
    """
    n = read_boot_count(nvs) + 1
    return n if _write(nvs, BOOT_COUNT_KEY, n) else 0


def clear_boot_count(nvs):
    """Mark this boot healthy. No-op when already zero (saves a write)."""
    if read_boot_count(nvs) == 0:
        return
    _write(nvs, BOOT_COUNT_KEY, 0)


def read_contactor_ops(nvs):
    return _read(nvs, CONTACTOR_OPS_KEY)


def bump_contactor_ops(nvs):
    """Count one contactor closure. Call ONLY on an OFF->ON transition."""
    n = read_contactor_ops(nvs) + 1
    return n if _write(nvs, CONTACTOR_OPS_KEY, n) else 0


def reset_contactor_ops(nvs):
    """Called by a technician after physically replacing the contactor."""
    _write(nvs, CONTACTOR_OPS_KEY, 0)
