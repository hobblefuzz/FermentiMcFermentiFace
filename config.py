"""NVS-backed configuration for the fermenter controller.

All values are stored as int32 in ESP32 NVS.  Floats are stored as
fixed-point (value × 10) and booleans as 0/1.

Access:  cfg.rh_lower          → int
Set:     cfg.rh_lower = 70     → persisted immediately
Reset:   cfg.reset()           → all values back to factory defaults
"""

import esp32

# ---------------------------------------------------------------------------
# Factory defaults
# ---------------------------------------------------------------------------
# key                 default   type hint   range / unit
DEFAULTS = {
    # Global
    "ctrl_enabled":     1,    # bool        overall on/off
    "use_fahrenheit":   0,    # bool        0=°C, 1=°F
    "interval_s":      10,    # int         1-60 s
    "scr_timeout_s":30,    # int         5-120 s
    "scr_always_on": 0,    # bool        if True, ignore timeout
    "chamber_max":45,    # int         30-80 °C  BME280 overtemp cutoff
    "wdt_enabled":      0,    # bool        hardware watchdog (applies on reboot)
    "wifi_en":          0,    # bool        connect to WiFi (creds in secrets.py)
    "ext_ctrl":         0,    # bool        apply setpoints from the Pi (external)
    "utc_offset_h":     2,    # int         -12..14  local-time offset for the clock display (UTC+h)

    # Heating (relay) — Ziel-T setpoint scheme on the DS18B20 core temperature.
    # One target + three deltas (all °C, stored ×10):
    #   dt_lower : core hysteresis  — heater ON when core ≤ target − dt_lower, OFF at target
    #   dt_floor : chamber floor    — also heat when chamber (BME280) ≤ target − dt_floor
    #              (arrests the core undershoot after fan cooling has chilled the air)
    #   dt_ceil  : chamber ceiling  — LATCHING overshoot brake: block heating when
    #              chamber ≥ target + dt_ceil, release only when chamber ≤ target.
    # Constraint enforced in GUI: target ≤ temp_lower (deadband vs. cooling).
    # Runtime safety gate: heater forced OFF once core ≥ target.
    "heat_enabled":     0,    # bool        default OFF until sensor wired
    "target_x10":     300,    # int         0-800 (= 30.0 °C) core target temperature
    "dt_lower_x10":     5,    # int         1-50  (= 0.5 °C)  core hysteresis band
    "dt_floor_x10":    20,    # int         0-100 (= 2.0 °C)  chamber floor below target
    "dt_ceil_x10":     20,    # int         1-100 (= 2.0 °C)  chamber ceiling above target
    "relay_min_s":     60,    # int         10-600 s anti-short-cycle

    # RH Control
    "rh_enabled":       1,    # bool
    "rh_lower":        85,    # int         0-100 %RH
    "rh_upper":        90,    # int         0-100 %RH
    "rh_pwm_f1":       50,    # int         1-100 %
    "rh_pwm_f2":       35,    # int         1-100 %

    # Temperature Control (fan cooling)  — stored as °C × 10
    "temp_enabled":     1,    # bool
    "temp_lower_x10": 310,    # int         0-800 (= 31.0 °C) fans OFF below (core)
    "temp_upper_x10": 320,    # int         0-800 (= 32.0 °C) fans ON above (core)
    "temp_pwm_f1":    100,    # int         1-100 %
    "temp_pwm_f2":    100,    # int         1-100 %

    # IDLE ventilation
    "idle_enabled":     1,    # bool
    "idle_max_idle":   30,    # int         2-60 min
    "idle_duration":   30,    # int         5-120 s
    "idle_pwm_f1":     35,    # int         1-100 %
    "idle_pwm_f2":     50,    # int         1-100 %
}

_BOOL_KEYS = frozenset(
    k for k, v in DEFAULTS.items()
    if k.endswith("_enabled")
    or k in ("ctrl_enabled", "use_fahrenheit", "scr_always_on", "wifi_en", "ext_ctrl")
)

# ESP32 NVS keys are limited to 15 characters.  An over-long key makes
# set_i32/get_i32 raise — which previously surfaced as a freeze on the first
# save.  Fail loudly at import instead, so it's caught the moment you flash.
for _k in DEFAULTS:
    if len(_k) > 15:
        raise ValueError("NVS key too long (>15 chars): " + _k)

_NVS_NS = "fermenter"


class Config:
    """Persistent configuration via ESP32 NVS with attribute-style access."""

    def __init__(self):
        # Use object.__setattr__ to bypass our own __setattr__ during init.
        object.__setattr__(self, "_nvs",   esp32.NVS(_NVS_NS))
        object.__setattr__(self, "_cache", {})
        self._load_all()

    # ---------------------------------------------------------------- load
    # Normal attribute access works fine — Python's lookup checks __dict__
    # first, so self._cache / self._nvs resolve before __getattr__ fires.
    def _load_all(self):
        for key, default in DEFAULTS.items():
            try:
                self._cache[key] = self._nvs.get_i32(key)
            except OSError:
                self._cache[key] = default

    # ----------------------------------------------- attribute access magic
    def __getattr__(self, name):
        if name not in DEFAULTS:
            raise AttributeError(name)
        val = self._cache.get(name, DEFAULTS[name])
        return bool(val) if name in _BOOL_KEYS else val

    def __setattr__(self, name, value):
        if name not in DEFAULTS:
            object.__setattr__(self, name, value)
            return
        int_val = int(value)
        self._cache[name] = int_val
        self._nvs.set_i32(name, int_val)
        self._nvs.commit()

    # ---------------------------------------------- temperature convenience
    @property
    def temp_lower(self) -> float:
        return self.temp_lower_x10 / 10.0

    @temp_lower.setter
    def temp_lower(self, c: float):
        self.temp_lower_x10 = round(c * 10)

    @property
    def temp_upper(self) -> float:
        return self.temp_upper_x10 / 10.0

    @temp_upper.setter
    def temp_upper(self, c: float):
        self.temp_upper_x10 = round(c * 10)

    @property
    def target(self) -> float:
        return self.target_x10 / 10.0

    @target.setter
    def target(self, c: float):
        self.target_x10 = round(c * 10)

    @property
    def dt_lower(self) -> float:
        return self.dt_lower_x10 / 10.0

    @dt_lower.setter
    def dt_lower(self, c: float):
        self.dt_lower_x10 = round(c * 10)

    @property
    def dt_floor(self) -> float:
        return self.dt_floor_x10 / 10.0

    @dt_floor.setter
    def dt_floor(self, c: float):
        self.dt_floor_x10 = round(c * 10)

    @property
    def dt_ceil(self) -> float:
        return self.dt_ceil_x10 / 10.0

    @dt_ceil.setter
    def dt_ceil(self, c: float):
        self.dt_ceil_x10 = round(c * 10)

    # ------------------------------------------------ volatile (remote) values
    def set_volatile(self, name, value):
        """Set a value in RAM only — no NVS write.  Used for setpoints pushed
        from the Pi, so remote control neither wears the flash nor clobbers the
        persisted local configuration."""
        if name in DEFAULTS:
            self._cache[name] = int(value)

    def reload(self):
        """Discard any volatile overrides and reload persisted NVS values."""
        self._load_all()

    # ---------------------------------------------------------------- reset
    def reset(self):
        """Restore all values to factory defaults."""
        for key, val in DEFAULTS.items():
            self._cache[key] = val
            self._nvs.set_i32(key, val)
        self._nvs.commit()

    def dump(self):
        """Print all current values (debug helper)."""
        for k in DEFAULTS:
            print(f"  {k} = {self._cache.get(k, DEFAULTS[k])}")
