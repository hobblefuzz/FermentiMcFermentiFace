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

    # Heating (relay) — references the DS18B20 core temperature.
    # Inverted hysteresis: ON below heat_lower, OFF above heat_upper.
    # Constraint enforced in GUI: heat_upper <= temp_lower (deadband).
    # min_room is a general room floor (may sit below the core band); the
    # runtime gate "no heating once core >= heat_upper" is the safety.
    "heat_enabled":     0,    # bool        default OFF until sensor wired
    "heat_lower_x10": 290,    # int         0-800 (= 29.0 °C) heater ON below (core)
    "heat_upper_x10": 300,    # int         0-800 (= 30.0 °C) heater OFF above (core)
    "min_room_x10":   280,    # int         0-800 (= 28.0 °C) room floor: heat if
                              #             chamber (BME280) drops below this
    "relay_min_s":60,   # int         10-600 s anti-short-cycle

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
    def heat_lower(self) -> float:
        return self.heat_lower_x10 / 10.0

    @heat_lower.setter
    def heat_lower(self, c: float):
        self.heat_lower_x10 = round(c * 10)

    @property
    def heat_upper(self) -> float:
        return self.heat_upper_x10 / 10.0

    @heat_upper.setter
    def heat_upper(self, c: float):
        self.heat_upper_x10 = round(c * 10)

    @property
    def min_room(self) -> float:
        return self.min_room_x10 / 10.0

    @min_room.setter
    def min_room(self, c: float):
        self.min_room_x10 = round(c * 10)

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
