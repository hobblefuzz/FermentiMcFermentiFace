"""BME280 sensor manager with unit conversion and smoothing.

Usage:
    s = SensorManager(i2c, addr=0x76)
    temp_c, temp_f, rh, pres_hpa = s.read()

The last successful reading is always available in:
    s.temperature_c, s.humidity, s.pressure_hpa
"""

from lib.bme280 import BME280


class SensorManager:
    def __init__(self, i2c, addr: int = 0x76):
        self._bme = BME280(i2c, addr=addr)
        self.temperature_c : float = 0.0
        self.temperature_f : float = 32.0
        self.humidity      : float = 0.0
        self.pressure_hpa  : float = 1013.25
        self._error        : bool  = False

    # ----------------------------------------------------------------- API
    def read(self) -> tuple:
        """Read sensor and return (temp_c, temp_f, humidity, pressure_hpa).

        Tolerant of transient I2C errors — retries up to 3 times before
        giving up and keeping the last good value.
        """
        import time
        last_err = None
        for _ in range(3):
            try:
                t_c, pres, hum = self._bme.read()
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

    @property
    def ok(self) -> bool:
        return not self._error

    def temperature(self, fahrenheit: bool = False) -> float:
        return self.temperature_f if fahrenheit else self.temperature_c
