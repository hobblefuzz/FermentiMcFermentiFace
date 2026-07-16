"""Heating relay control with fail-safe boot state and anti-short-cycle.

The relay drives a mains heating mat.  Two safety principles:

1. Fail-safe at boot: the control pin is driven to the OFF level the instant
   this object is created — before any control logic runs.
2. Relay protection: request() enforces a minimum time between switching
   events so the mechanical relay can't chatter itself to death.  The
   off() method bypasses this for safety cut-offs (sensor loss, overtemp).

Active-high vs active-low is configurable: many cheap relay modules energise
on a LOW input.  Set active_low=True for those so "off" drives the pin HIGH.
"""

import time
from machine import Pin


class Heater:
    def __init__(self, pin: int, active_low: bool = False, min_cycle_s: int = 60):
        self._on_val   = 0 if active_low else 1
        self._off_val  = 1 if active_low else 0
        # Drive to OFF immediately — fail-safe before any logic runs
        self._pin = Pin(pin, Pin.OUT, value=self._off_val)
        self._is_on        = False
        # Start the switch timer in the past so the FIRST switch isn't blocked
        # by the anti-short-cycle window (boot is not a real switching event).
        self._last_switch  = time.ticks_add(time.ticks_ms(), -min_cycle_s * 1000)
        self._on_since     = 0
        self.min_cycle_s   = min_cycle_s

    # ---------------------------------------------------------------- private
    def _switch(self, on: bool):
        self._pin.value(self._on_val if on else self._off_val)
        self._is_on       = on
        self._last_switch = time.ticks_ms()
        if on:
            self._on_since = self._last_switch

    # ------------------------------------------------------------------- API
    def request(self, want_on: bool) -> bool:
        """Request a state change, honouring the anti-short-cycle timer.

        Returns the *actual* state (which may differ if a switch was blocked).
        """
        if want_on == self._is_on:
            return self._is_on
        since_switch = time.ticks_diff(time.ticks_ms(), self._last_switch) / 1000
        if since_switch < self.min_cycle_s:
            return self._is_on        # too soon — hold current state
        self._switch(want_on)
        return self._is_on

    def off(self):
        """Force OFF immediately, bypassing anti-short-cycle (safety cut-off)."""
        if self._is_on:
            self._switch(False)

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def on_duration_s(self) -> float:
        """Seconds the heater has been continuously on (0 if off)."""
        if not self._is_on:
            return 0
        return time.ticks_diff(time.ticks_ms(), self._on_since) / 1000

    def deinit(self):
        self.off()
