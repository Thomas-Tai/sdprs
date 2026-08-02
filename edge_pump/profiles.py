# -*- coding: utf-8 -*-
"""Actuator profile parameter table (spec §5.2).

A profile answers ONE question: what is on the other side of GPIO 33, and
therefore how gently must it be treated. It carries safety timing and which
peripherals exist.

It must NEVER carry anything about what the water MEANS — that is PUMP_MODE's
job, and the two axes are deliberately orthogonal (spec §3). If you find
yourself wanting `if profile == ... and mode == ...`, the logic belongs in
neither and the split is wrong.

Desktop-importable: no `machine`, no `esp32`.
"""

PUMP_12V = {
    # 0 disables the Layer 3.5 guard entirely — a 12V DC pump has no
    # start-frequency limit worth protecting.
    "min_off_ms": 0,
    "burst_cooldown_ms": 30000,
    "boot_holdoff_ms": 0,
    "boot_holdoff_urgent_ms": 0,
    "boot_loop_holdoff_ms": 0,
    "boot_loop_threshold": 0,        # 0 disables reset-loop detection
    "boot_healthy_ms": 0,
    "ct_enabled": False,
    "hoa_enabled": False,
    "wdt_required": False,
    "contactor_service_ops": 0,      # 0 disables the service-due counter
}

SOCKET_220V = {
    # Small AC pumps tolerate roughly 10-20 starts/hour. 180s == 20/hr.
    "min_off_ms": 180000,
    # Layer 1 bursts bypass min-off by design, so the cooldown must itself
    # be long enough to be safe for an AC motor.
    "burst_cooldown_ms": 180000,
    "boot_holdoff_ms": 60000,
    # A flat 60s would refuse to pump while water rises after a brownout
    # reset — the exact scenario the node exists for (spec §5.6).
    "boot_holdoff_urgent_ms": 10000,
    # In a confirmed reset loop, urgency does NOT shorten the hold-off:
    # a node rebooting every 30s cannot be trusted to have read its
    # sensors correctly. 5 min breaks the WDT loop cycle decisively while
    # still returning a genuinely-recovered node within one surge window.
    "boot_loop_holdoff_ms": 300000,
    "boot_loop_threshold": 3,
    "boot_healthy_ms": 300000,
    "ct_enabled": True,
    "hoa_enabled": True,
    "wdt_required": True,
    # Placeholder pending the purchased contactor's AC3 electrical life
    # (spec §10 open item 5). 60_000 == 60% of a 100k-operation part.
    "contactor_service_ops": 60000,
}

_PROFILES = {"PUMP_12V": PUMP_12V, "SOCKET_220V": SOCKET_220V}


def get_profile(name):
    """Return a COPY of the named profile. Raises ValueError if unknown."""
    if name not in _PROFILES:
        raise ValueError("unknown ACTUATOR_PROFILE: %r" % (name,))
    return dict(_PROFILES[name])


def validate(profile, wdt_enabled):
    """Raise ValueError if the runtime configuration contradicts the profile.

    Called at boot, before the relay is ever driven. Failing loudly here is
    the point: a misconfigured mains node must not reach the control loop.
    """
    if profile["wdt_required"] and not wdt_enabled:
        raise ValueError(
            "ACTUATOR_PROFILE requires WDT_ENABLED=True: a hung controller "
            "would hold the contactor closed indefinitely")
