# BME280 MicroPython driver
# Adapted from Robert Hammelrath's implementation (MIT License)
# https://github.com/robert-hh/BME280

import time
from ustruct import unpack, unpack_from

BME280_I2C_ADDR_PRIM = 0x76
BME280_I2C_ADDR_SEC  = 0x77

_REG_RESET    = 0xE0
_REG_CTRL_HUM = 0xF2
_REG_STATUS   = 0xF3
_REG_CTRL_MEAS= 0xF4
_REG_CONFIG   = 0xF5
_REG_DATA     = 0xF7   # 8 bytes: press(3) temp(3) hum(2)
_REG_CALIB1   = 0x88   # 24 bytes
_REG_CALIB2   = 0xA1   # 1 byte (H1)
_REG_CALIB3   = 0xE1   # 7 bytes (H2-H6)
_REG_CHIP_ID  = 0xD0

OVERSAMPLE_OFF = 0
OVERSAMPLE_1   = 1
OVERSAMPLE_2   = 2
OVERSAMPLE_4   = 3
OVERSAMPLE_8   = 4
OVERSAMPLE_16  = 5

MODE_SLEEP  = 0
MODE_FORCED = 1
MODE_NORMAL = 3

STANDBY_0_5  = 0
STANDBY_62_5 = 1
STANDBY_125  = 2
STANDBY_250  = 3
STANDBY_500  = 4
STANDBY_1000 = 5

FILTER_OFF = 0
FILTER_2   = 1
FILTER_4   = 2
FILTER_8   = 3
FILTER_16  = 4


class BME280:
    def __init__(self, i2c, addr=BME280_I2C_ADDR_PRIM,
                 osample_temp=OVERSAMPLE_2,
                 osample_pres=OVERSAMPLE_2,
                 osample_hum=OVERSAMPLE_2,
                 standby=STANDBY_250,
                 iir_filter=FILTER_4,
                 mode=MODE_NORMAL):
        self._i2c = i2c
        self._addr = addr
        self._t_fine = 0

        # Sensor power-on tolerates a few I2C retries during cold boot
        time.sleep_ms(5)
        chip_id = None
        for _ in range(5):
            try:
                chip_id = self._read_byte(_REG_CHIP_ID)
                break
            except OSError:
                time.sleep_ms(10)
        if chip_id is None:
            raise OSError("BME280: no response on I2C")
        if chip_id not in (0x60, 0x58):
            raise RuntimeError(f"BME280: unexpected chip id 0x{chip_id:02X}")

        self._read_calibration()

        # Humidity oversampling must be set before ctrl_meas
        self._write(_REG_CTRL_HUM, osample_hum & 0x07)
        # Config: standby time + IIR filter (SPI 3-wire off)
        self._write(_REG_CONFIG, ((standby & 0x07) << 5) | ((iir_filter & 0x07) << 2))
        # Temp + pressure oversampling + mode
        self._write(_REG_CTRL_MEAS,
                    ((osample_temp & 0x07) << 5) |
                    ((osample_pres & 0x07) << 2) |
                    (mode & 0x03))

    # ------------------------------------------------------------------ helpers
    def _read_byte(self, reg):
        return self._i2c.readfrom_mem(self._addr, reg, 1)[0]

    def _read(self, reg, n):
        return self._i2c.readfrom_mem(self._addr, reg, n)

    def _write(self, reg, value):
        self._i2c.writeto_mem(self._addr, reg, bytes([value & 0xFF]))

    # ------------------------------------------------------------ calibration
    def _read_calibration(self):
        c1 = self._read(_REG_CALIB1, 24)
        c2 = self._read_byte(_REG_CALIB2)
        c3 = self._read(_REG_CALIB3, 7)

        self._T = unpack_from('<Hhh', c1, 0)          # T1 uint16, T2/T3 int16
        self._P = unpack_from('<Hhhhhhhhh', c1, 6)    # P1 uint16, P2-P9 int16
        self._H1 = c2
        self._H2 = unpack_from('<h', c3, 0)[0]        # int16
        self._H3 = c3[2]                               # uint8
        # H4 / H5 share a nibble byte
        self._H4 = (c3[3] << 4) | (c3[4] & 0x0F)
        if self._H4 > 2047:
            self._H4 -= 4096
        self._H5 = (c3[5] << 4) | (c3[4] >> 4)
        if self._H5 > 2047:
            self._H5 -= 4096
        self._H6 = unpack_from('b', c3, 6)[0]         # int8

    # ------------------------------------------------------ compensated values
    def _compensate_temp(self, adc_T):
        T1, T2, T3 = self._T
        var1 = (adc_T / 16384.0 - T1 / 1024.0) * T2
        var2 = ((adc_T / 131072.0 - T1 / 8192.0) ** 2) * T3
        self._t_fine = int(var1 + var2)
        return (var1 + var2) / 5120.0

    def _compensate_pressure(self, adc_P):
        P1, P2, P3, P4, P5, P6, P7, P8, P9 = self._P
        var1 = self._t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * P6 / 32768.0
        var2 += var1 * P5 * 2.0
        var2 = var2 / 4.0 + P4 * 65536.0
        var1 = (P3 * var1 * var1 / 524288.0 + P2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * P1
        if var1 == 0:
            return 0
        p = 1048576.0 - adc_P
        p = ((p - var2 / 4096.0) * 6250.0) / var1
        var1 = P9 * p * p / 2147483648.0
        var2 = p * P8 / 32768.0
        return p + (var1 + var2 + P7) / 16.0

    def _compensate_humidity(self, adc_H):
        h = self._t_fine - 76800.0
        if h == 0:
            return 0
        h = (adc_H - (self._H4 * 64.0 + self._H5 / 16384.0 * h)) * \
            (self._H2 / 65536.0 * (1.0 + self._H6 / 67108864.0 * h *
             (1.0 + self._H3 / 67108864.0 * h)))
        h *= 1.0 - self._H1 * h / 524288.0
        return max(0.0, min(100.0, h))

    # ------------------------------------------------------------------- read
    def read(self):
        """Return (temperature_C, pressure_hPa, humidity_percent) as floats."""
        raw = self._read(_REG_DATA, 8)
        adc_P = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        adc_T = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        adc_H = (raw[6] << 8) | raw[7]

        temp = self._compensate_temp(adc_T)
        pres = self._compensate_pressure(adc_P) / 100.0
        hum  = self._compensate_humidity(adc_H)
        return temp, pres, hum
