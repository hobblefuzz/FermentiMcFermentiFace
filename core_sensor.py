"""DS18B20 core-temperature sensor (1-Wire), non-blocking.

The DS18B20 needs ~750 ms for a 12-bit conversion.  Blocking the main loop
for that long would freeze the GUI, so reading is split into three steps:

    if core.start():          # kick off conversion (non-blocking)
        ...
    if core.ready():          # 750 ms elapsed?
        temp = core.read()    # returns averaged °C, or None on fault

Multiple sensors on the same 1-Wire pin are supported (each has a unique
64-bit ROM address).  read() averages all sensors that return a *plausible*
value and rejects faults:
  • CRC errors / read exceptions
  • -127 °C (classic disconnect value)
  • exactly 85.0 °C (power-on reset default — implausible for a ferment)
  • anything outside the sensor's -55…125 °C range

On any fault read() returns None and .ok becomes False, so the controller
can fail safe (heater off).
"""

import time
import onewire
import ds18x20
from machine import Pin

_RESET_VAL   = 85.0     # DS18B20 power-on reset reading
_NEAR_ZERO   = 0.5      # |t| below this = all-zeros bus glitch (passes CRC!)
_FAULT_AFTER = 3        # consecutive failed read cycles before declaring fault
_BUS_RETRIES = 5        # fast retries on a marginal OneWire reset (~1 ms each)

# Resolution → (config register byte, conversion time ms incl. margin)
#  9-bit=0.5°C  10-bit=0.25°C  11-bit=0.125°C  12-bit=0.0625°C
_RES = {
    9:  (0x1F, 100),
    10: (0x3F, 200),
    11: (0x5F, 400),
    12: (0x7F, 750),
}


class CoreTempSensor:
    def __init__(self, pin: int, resolution: int = 11):
        self._ow = onewire.OneWire(Pin(pin))
        self._ds = ds18x20.DS18X20(self._ow)
        self._roms = []
        self._conv_started = 0
        self._converting   = False
        self._fail_count   = 0
        self.temperature_c = None    # last valid averaged temperature
        self._error        = True
        self._cfg_byte, self._conv_ms = _RES.get(resolution, _RES[11])
        self.rescan()
        self._apply_resolution()

    def _apply_resolution(self):
        """Write the resolution config byte to each sensor.  Scratchpad-only
        (no Copy Scratchpad) → re-applied each boot, so no EEPROM wear."""
        buf = b'\x00\x00' + bytes([self._cfg_byte])   # TH, TL, config
        for rom in self._roms:
            for _ in range(_BUS_RETRIES):
                try:
                    self._ds.write_scratch(rom, buf)
                    break
                except Exception:
                    time.sleep_ms(5)

    # ----------------------------------------------------------------- scan
    def rescan(self):
        """(Re)discover sensors on the bus. Keeps the old ROM list if a scan
        comes back empty, so a single glitched scan doesn't lose the sensor."""
        try:
            found = self._ds.scan()
            if found:
                self._roms = found
        except Exception:
            pass
        return len(self._roms)

    @property
    def sensor_count(self) -> int:
        return len(self._roms)

    # ------------------------------------------------------ non-blocking read
    def start(self) -> bool:
        """Begin a conversion. Returns True if a conversion is now pending.

        The OneWire reset inside convert_temp() is marginal on longer cables
        and throws OneWireError intermittently.  The reset is only ~1 ms, so
        we retry it quickly — a few attempts turn a flaky bus reliable without
        the cost of re-running the 750 ms conversion.
        """
        if not self._roms and self.rescan() == 0:
            return False
        for _ in range(_BUS_RETRIES):
            try:
                self._ds.convert_temp()
                self._conv_started = time.ticks_ms()
                self._converting   = True
                return True
            except Exception:
                time.sleep_ms(5)
        return False        # convert never started this cycle

    def ready(self) -> bool:
        return self._converting and \
            time.ticks_diff(time.ticks_ms(), self._conv_started) >= self._conv_ms

    def read(self):
        """Read all sensors after ready(); return averaged °C or None.

        Per-sensor reads are retried on bus exceptions (the scratchpad
        persists after conversion, so a re-read costs ~1 ms).  Transient
        glitches keep the last good value; only _FAULT_AFTER consecutive
        failed cycles declare a fault (None, .ok False).
        """
        self._converting = False
        vals = []
        for rom in self._roms:
            for _ in range(_BUS_RETRIES):
                try:
                    t = self._ds.read_temp(rom)
                except Exception:
                    time.sleep_ms(5)
                    continue                     # bus reset glitch — retry
                # Reject implausible values and RETRY (re-reading the scratchpad
                # often recovers a glitched transfer).  Critically this includes
                # ~0.0 °C: an all-zeros read passes CRC and would look "cold",
                # switching the heater on — a dangerous failure mode.
                if (t is None or t <= -55.0 or t >= 125.0
                        or abs(t - _RESET_VAL) < 0.01 or abs(t) < _NEAR_ZERO):
                    time.sleep_ms(5)
                    continue
                vals.append(t)
                break

        if vals:
            return self._register_ok(round(sum(vals) / len(vals), 1))
        # No valid reading: hold the last good value (grace), then fault.
        return self.on_cycle_fail()

    # ------------------------------------------------------ fault accounting
    def _register_ok(self, temp):
        self._fail_count   = 0
        self._error        = False
        self.temperature_c = temp
        return temp

    def on_cycle_fail(self):
        """Register a failed sensing cycle (also called by main loop when the
        conversion couldn't even be started).  Returns the graced value, or
        None once the fault threshold is crossed."""
        self._fail_count += 1
        if self._fail_count == 2:
            self.rescan()                        # try to recover a glitched bus
        if self._fail_count >= _FAULT_AFTER:
            self._error        = True
            self.temperature_c = None
            return None
        return self.temperature_c                # grace: last good value

    @property
    def ok(self) -> bool:
        return not self._error
