# -*- coding: utf-8 -*-
"""Pump relay + LED actuation, and the stateful timing/CtrlState manager
that feeds control_logic.decide(). All ticks bookkeeping lives here."""

import control_logic


class _RealClock:
    def ticks_ms(self):
        import time
        return time.ticks_ms()

    def ticks_diff(self, a, b):
        import time
        return time.ticks_diff(a, b)


class PumpController:
    def __init__(self, relay, led_red, led_green, config, clock=None):
        self._relay = relay
        self._led_red = led_red
        self._led_green = led_green
        self._config = config
        self._clock = clock or _RealClock()

        self.ctrl_state = control_logic.initial_state()
        self._on_since = None
        self._off_since = None      # start of the current continuous-OFF period
        self._rain_wet_since = None
        self._level_low_since = None
        self._burst_phase_since = None
        self._conflict_since = None

        self._apply_relay("OFF")  # safe initial state

    @property
    def state(self):
        return self.ctrl_state["pump_state"]

    def _apply_relay(self, action):
        if action == "ON":
            self._relay.value(1)
            self._led_red.value(1)
            self._led_green.value(0)
        else:  # OFF
            self._relay.value(0)
            self._led_red.value(0)
            self._led_green.value(1)

    def _elapsed(self, since, now):
        return None if since is None else self._clock.ticks_diff(now, since)

    def snapshot_timing(self, readings):
        now = self._clock.ticks_ms()

        if readings.get("raining") is True:
            if self._rain_wet_since is None:
                self._rain_wet_since = now
        else:
            self._rain_wet_since = None

        level = readings.get("level_pct")
        low = self._config["low_threshold"]
        if level is not None and level <= low:
            if self._level_low_since is None:
                self._level_low_since = now
        else:
            self._level_low_since = None

        return {
            "pump_on_elapsed_ms": self._elapsed(self._on_since, now),
            "rain_wet_elapsed_ms": self._elapsed(self._rain_wet_since, now),
            "level_low_elapsed_ms": self._elapsed(self._level_low_since, now),
            "burst_phase_elapsed_ms": self._elapsed(self._burst_phase_since, now),
            "conflict_elapsed_ms": self._elapsed(self._conflict_since, now),
            # rest_elapsed_ms = continuous-OFF duration, so decide()'s max-runtime
            # rest (Layer 3) measures ACTUAL rest: a conflict burst that runs the
            # pump mid-rest clears _off_since and restarts the rest.
            "rest_elapsed_ms": self._elapsed(self._off_since, now),
        }

    def apply(self, decision):
        now = self._clock.ticks_ms()
        prev = self.ctrl_state
        nxt = decision["next_state"]
        action = decision["action"]

        # actuation
        if action == "ON":
            self._apply_relay("ON")
        elif action == "OFF":
            self._apply_relay("OFF")
        # HOLD: leave relay as-is

        # pump-on / continuous-off timers
        if nxt["pump_state"] == "ON":
            if prev["pump_state"] != "ON":
                self._on_since = now
            self._off_since = None            # running -> not resting
        else:  # nxt OFF
            if prev["pump_state"] == "ON":
                self._off_since = now          # ON->OFF: start the rest/off clock
            self._on_since = None

        # burst-phase timer
        if nxt.get("burst_phase") != prev.get("burst_phase"):
            self._burst_phase_since = now if nxt.get("burst_phase") is not None else None

        # conflict timer
        if nxt.get("conflict_latched") and not prev.get("conflict_latched"):
            self._conflict_since = now
        elif not nxt.get("conflict_latched"):
            self._conflict_since = None

        self.ctrl_state = nxt
