"""Ventilation + heating controller.

Temperature sources
-------------------
 - core_temp_c    : DS18B20 inside the ferment  → drives heating AND fan cooling
 - chamber_temp_c : BME280 chamber air          → independent overtemp safety net
 - humidity       : BME280                       → RH ventilation

Fan logic (OR for ON, AND for OFF), all on core temp:
 - RH control:   fan on  when RH ≥ rh_upper,        off when RH ≤ rh_lower
 - Temp control: fan on  when core ≥ temp_upper,    off when core ≤ temp_lower
 - IDLE:         fan on  after idle_max_idle min idle, off after idle_duration

Heating logic (relay) — Ziel-T target + three deltas:
 - core hysteresis:  on when core ≤ target − dt_lower,  off when core ≥ target
 - chamber floor:    on when chamber ≤ target − dt_floor  (arrests the core
                     undershoot when fan cooling has chilled the chamber air)
 - chamber ceiling:  LATCHING overshoot brake — heating is blocked once chamber
                     ≥ target + dt_ceil and released only when chamber ≤ target.
                     Caps the fast chamber excursion so the slow core can't
                     overshoot the target by more than ~dt_ceil, regardless of
                     the chamber→core dead-time.
 - SAFETY GATE:      the heater is forced OFF whenever core ≥ target — so no
                     trigger can push the core past its target.
 The GUI enforces target ≤ temp_lower (deadband vs. cooling).

Safety
------
 - core_temp_c is None (sensor fault) → heater forced OFF, temp-fan disabled
 - chamber_temp_c ≥ chamber_max  → heater forced OFF + both fans 100 %
 The active PWM for each fan is the maximum of all active conditions.
"""

import time

_ROOM_HYST = 0.5   # °C hysteresis for the room-temperature floor


class VentState:
    """Bitmask constants for which condition triggered the fans."""
    NONE = 0
    RH   = 1
    TEMP = 2
    IDLE = 4


class VentilationController:
    def __init__(self, config, fan1, fan2, heater=None):
        self.config = config
        self.fan1   = fan1
        self.fan2   = fan2
        self.heater = heater

        self._rh_active   = False
        self._temp_active = False
        self._idle_active = False
        self._heat_core   = False   # core-temp heating hysteresis state
        self._heat_room   = False   # chamber-floor heating hysteresis state
        self._heat_block  = False   # chamber-ceiling overshoot brake (latching)
        self._heat_want   = False

        self._last_fan_on  = time.time()
        self._idle_started = 0
        self._active_state = VentState.NONE

        self._core_fault    = True    # no reading yet
        self._chamber_alarm = False
        self._emergency     = False

    # ----------------------------------------------------------------- API
    def update(self, core_temp_c, chamber_temp_c, humidity):
        """Evaluate all conditions and drive fans + heater.

        core_temp_c / chamber_temp_c may be None on sensor fault.
        Returns the current fan VentState bitmask.
        """
        cfg = self.config
        now = time.time()

        self._core_fault    = core_temp_c is None
        self._chamber_alarm = (chamber_temp_c is not None and
                               chamber_temp_c >= cfg.chamber_max)

        # ---- Master switch off → everything safe-off
        if not cfg.ctrl_enabled:
            self._rh_active = self._temp_active = self._idle_active = False
            self._heat_core = self._heat_room = self._heat_want = False
            self._heat_block = False
            self._emergency = False
            if self.heater:
                self.heater.off()
            self._active_state = VentState.NONE
            self._apply_fans()
            return VentState.NONE

        # ---- Heating (relay) — chamber overtemp and core fault force OFF
        if self.heater is not None:
            self.heater.min_cycle_s = cfg.relay_min_s   # apply menu changes live
            if self._chamber_alarm or self._core_fault or not cfg.heat_enabled:
                self._heat_core = self._heat_room = self._heat_want = False
                self._heat_block = False
                self.heater.off()
            else:
                target = cfg.target

                # Core-temperature hysteresis: on below target−dt_lower, off at target
                if core_temp_c <= target - cfg.dt_lower:
                    self._heat_core = True
                elif core_temp_c >= target:
                    self._heat_core = False

                # Chamber floor (BME280): heat if the air has been chilled below
                # target−dt_floor.  Only on a valid reading.
                if chamber_temp_c is not None:
                    floor = target - cfg.dt_floor
                    if chamber_temp_c <= floor:
                        self._heat_room = True
                    elif chamber_temp_c >= floor + _ROOM_HYST:
                        self._heat_room = False

                    # Chamber ceiling — latching overshoot brake: block once the
                    # air runs away above target+dt_ceil, release at target.
                    if chamber_temp_c >= target + cfg.dt_ceil:
                        self._heat_block = True
                    elif chamber_temp_c <= target:
                        self._heat_block = False
                else:
                    # No chamber reading → can't run the floor or the brake.
                    self._heat_room  = False
                    self._heat_block = False

                # Combine; the brake caps the chamber excursion, then the safety
                # gate makes sure nothing pushes the core past its target.
                want = self._heat_core or self._heat_room
                if self._heat_block:
                    want = False
                if core_temp_c >= target:
                    want = False
                self._heat_want = want
                self.heater.request(want)

        # ---- RH hysteresis (BME280 humidity)
        if cfg.rh_enabled:
            if humidity >= cfg.rh_upper:
                self._rh_active = True
            elif humidity <= cfg.rh_lower:
                self._rh_active = False
        else:
            self._rh_active = False

        # ---- Temp hysteresis (fan cooling, on CORE temp)
        if cfg.temp_enabled and not self._core_fault:
            if core_temp_c >= cfg.temp_upper:
                self._temp_active = True
            elif core_temp_c <= cfg.temp_lower:
                self._temp_active = False
        else:
            self._temp_active = False

        # ---- IDLE ventilation
        if cfg.idle_enabled:
            if self._idle_active:
                if now - self._idle_started >= cfg.idle_duration:
                    self._idle_active = False
            else:
                if not (self._rh_active or self._temp_active):
                    if now - self._last_fan_on >= cfg.idle_max_idle * 60:
                        self._idle_active  = True
                        self._idle_started = now
        else:
            self._idle_active = False

        # ---- Combined fan state
        state = VentState.NONE
        if self._rh_active:   state |= VentState.RH
        if self._temp_active: state |= VentState.TEMP
        if self._idle_active: state |= VentState.IDLE

        if state != VentState.NONE:
            self._last_fan_on = now

        self._active_state = state
        self._emergency    = self._chamber_alarm   # forces fans to 100 %
        self._apply_fans()
        return state

    def force_off(self):
        """Immediately stop fans and heater."""
        self.fan1.off()
        self.fan2.off()
        if self.heater:
            self.heater.off()

    # ------------------------------------------------------------ properties
    @property
    def fans_running(self) -> bool:
        return self._active_state != VentState.NONE or self._emergency

    @property
    def active_state(self) -> int:
        return self._active_state

    @property
    def heater_on(self) -> bool:
        return self.heater.is_on if self.heater else False

    @property
    def core_fault(self) -> bool:
        return self._core_fault

    @property
    def chamber_alarm(self) -> bool:
        return self._chamber_alarm

    @property
    def idle_due_in(self) -> int:
        """Seconds until next idle ventilation (0 if already active)."""
        if self._idle_active:
            return 0
        remaining = self.config.idle_max_idle * 60 - (time.time() - self._last_fan_on)
        return max(0, int(remaining))

    # ---------------------------------------------------------------- helpers
    def _apply_fans(self):
        # Emergency: chamber overtemp → blast both fans regardless of settings
        if self._emergency:
            self.fan1.set_percent(100)
            self.fan2.set_percent(100)
            return

        state = self._active_state
        cfg   = self.config

        if state == VentState.NONE:
            self.fan1.off()
            self.fan2.off()
            return

        # Maximum PWM across all active conditions
        pwm1 = pwm2 = 0
        if state & VentState.RH:
            pwm1 = max(pwm1, cfg.rh_pwm_f1)
            pwm2 = max(pwm2, cfg.rh_pwm_f2)
        if state & VentState.TEMP:
            pwm1 = max(pwm1, cfg.temp_pwm_f1)
            pwm2 = max(pwm2, cfg.temp_pwm_f2)
        if state & VentState.IDLE:
            pwm1 = max(pwm1, cfg.idle_pwm_f1)
            pwm2 = max(pwm2, cfg.idle_pwm_f2)

        self.fan1.set_percent(pwm1)
        self.fan2.set_percent(pwm2)

    def status_str(self) -> str:
        """Compact fan status string (always exactly 3 chars).

        OFF, R--, -T-, --I, RT-, R-I, -TI, RTI  (EMG when emergency cooling)
        """
        if self._emergency:
            return "EMG"
        s = self._active_state
        if s == VentState.NONE:
            return "OFF"
        return ("R" if s & VentState.RH   else "-") + \
               ("T" if s & VentState.TEMP else "-") + \
               ("I" if s & VentState.IDLE else "-")
