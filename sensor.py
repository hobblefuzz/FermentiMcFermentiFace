"""BME280 sensor manager with unit conversion and spike filtering.

Usage:
    s = SensorManager(i2c, addr=0x76)
    temp_c, temp_f, rh, pres_hpa = s.read()

The last successful reading is always available in:
    s.temperature_c, s.humidity, s.pressure_hpa

Spike filter
------------
The BME280 occasionally returns fixed I2C-error sentinels (chamber ~188.1 C
and/or RH 100 %), always for ~2 consecutive reads, then recovers.  These pass
the driver's CRC (no exception), so the retry loop below does NOT catch them.
Left unfiltered they trip the chamber-overtemp cutoff (spurious EMG, fans 100 %)
and spurious RH venting.

A median window fails here (two bad reads in a row), so each channel instead
uses a rate-of-change filter with hold-last-good: a reading that jumps more than
_*_JUMP from the last good value (temp also: out of plausible range) is rejected
and the last good value is held.  After _MAX_HOLD consecutive rejects the new
value is adopted anyway, so a genuine level shift can't latch the filter forever.
Real chamber changes are far slower than the thresholds, and a genuine gradual
rise (incl. to 100 % RH or a real overtemp) passes through untouched.
"""

from lib.bme280 import BME280


class SensorManager:
    # --- spike-filter tuning (per read, ~5 s apart) ---
    _T_MIN, _T_MAX = -20.0, 80.0   # plausible chamber range (C); 188.1 glitch is far outside
    _T_JUMP        = 5.0           # max real chamber change per read (C) — glitch jump is ~160
    _RH_JUMP       = 10.0          # max real RH change per read (%) — glitch jump to 100 is ~15
    _MAX_HOLD      = 3             # adopt a persistent new level after this many held reads

    def __init__(self, i2c, addr: int = 0x76):
        self._bme = BME280(i2c, addr=addr)
        self.temperature_c : float = 0.0
        self.temperature_f : float = 32.0
        self.humidity      : float = 0.0
        self.pressure_hpa  : float = 1013.25
        self._error        : bool  = False
        # spike-filter state (per channel)
        self._lg_t   = None   # last good temperature (C)
        self._lg_rh  = None   # last good humidity (%)
        self._rej_t  = 0      # consecutive temp rejections
        self._rej_rh = 0      # consecutive RH rejections
        self.spikes  = 0      # total held (rejected) readings, for diagnostics

    # ----------------------------------------------------------------- API
    def read(self) -> tuple:
        """Read sensor and return (temp_c, temp_f, humidity, pressure_hpa).

        Tolerant of transient I2C errors — retries up to 3 times before
        giving up and keeping the last good value.  Successful reads are then
        passed through the per-channel spike filter (see module docstring).
        """
        import time
        last_err = None
        for _ in range(3):
            try:
                t_c, pres, hum = self._bme.read()
                t_c, self._lg_t, self._rej_t = self._despike(
                    t_c, self._lg_t, self._rej_t, self._T_JUMP,
                    "T", self._T_MIN, self._T_MAX)
                hum, self._lg_rh, self._rej_rh = self._despike(
                    hum, self._lg_rh, self._rej_rh, self._RH_JUMP, "RH")
                self.temperature_c = round(t_c, 1)
                self.temperature_f = round(t_c * 9 / 5 + 32, 1)
                self.humidity      = round(hum, 1)
                self.pressure_hpa  = round(pres, 1)
                self._error        = False
                return (self.temperature_c, self.temperature_f,
                        self.humidity, self.pressure_hpa)
            except Exception as e:
                last_err = e
                time.sleep_ms(5)
        self._error = True
        print(f"[Sensor] read error: {last_err}")
        return (self.temperature_c, self.temperature_f,
                self.humidity, self.pressure_hpa)

    # ------------------------------------------------------------- filtering
    def _despike(self, raw, last_good, rejects, jump, name, lo=None, hi=None):
        """Reject a single-channel spike, holding the last good value.

        Returns (value, new_last_good, new_rejects).  `lo`/`hi` add a plausible
        range gate (used for temperature; RH's valid range is always 0..100 so
        the jump test alone catches its 100 % glitch).
        """
        in_range = (lo is None) or (lo <= raw <= hi)
        if last_good is None:
            # Seed from the first plausible reading; ignore a boot-time glitch.
            return (raw, raw if in_range else None, 0)
        if in_range and abs(raw - last_good) <= jump:
            return (raw, raw, 0)                       # normal reading
        rejects += 1
        if rejects > self._MAX_HOLD and in_range:
            return (raw, raw, 0)                       # persistent new level → adopt
        self.spikes += 1
        print("[Sensor] %s spike %.1f rejected, holding %.1f" % (name, raw, last_good))
        return (last_good, last_good, rejects)         # spike → hold last good

    @property
    def ok(self) -> bool:
        return not self._error

    def temperature(self, fahrenheit: bool = False) -> float:
        return self.temperature_f if fahrenheit else self.temperature_c
