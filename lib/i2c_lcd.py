# I2C LCD driver using PCF8574 I/O expander
# Compatible with common 1602/2004 I2C LCD backpack modules
#
# PCF8574 → HD44780 pin mapping:
# Bit:  7   6   5   4   3   2   1   0
#       D7  D6  D5  D4  BL  EN  RW  RS

import time
from .lcd_api import LcdApi

MASK_RS = 0x01
MASK_RW = 0x02
MASK_EN = 0x04
MASK_BL = 0x08


class I2cLcd(LcdApi):
    def __init__(self, i2c, i2c_addr, num_lines=2, num_columns=16):
        self._i2c      = i2c
        self._i2c_addr = i2c_addr
        self._bl       = MASK_BL   # backlight flag

        time.sleep_ms(50)          # wait for LCD power-on

        # HD44780 init sequence in 4-bit mode:
        # Three times: send high nibble 0x3 (function-set 8-bit) to reset state
        for delay_ms in (5, 1, 1):
            self._write_nibble(0x30)   # 0x3 in high nibble, no RS
            time.sleep_ms(delay_ms)

        # Switch to 4-bit mode
        self._write_nibble(0x20)       # 0x2 in high nibble
        time.sleep_ms(1)

        super().__init__(num_lines, num_columns)

    # ----------------------------------------------------------------- HAL
    def _hal_write_command(self, cmd):
        self._send(cmd, rs=0)
        if cmd in (0x01, 0x02):
            time.sleep_ms(2)
        else:
            time.sleep_us(50)

    def _hal_write_data(self, data):
        self._send(data, rs=MASK_RS)
        time.sleep_us(50)

    def _hal_backlight_on(self):
        self._bl = MASK_BL
        self._write_pcf(self._bl)

    def _hal_backlight_off(self):
        self._bl = 0
        self._write_pcf(self._bl)

    # ---------------------------------------------------------------- private
    def _send(self, value, rs):
        """Send a full byte to the HD44780 as two 4-bit nibbles."""
        # High nibble first, then low nibble
        self._write_nibble((value & 0xF0) | rs)
        self._write_nibble(((value << 4) & 0xF0) | rs)

    def _write_nibble(self, byte):
        """Send one nibble (already in bits 7-4) with EN pulse.

        byte layout:  D7 D6 D5 D4 0 0 0 RS
        Backlight is OR'd in here so callers never need to set it.
        """
        data = byte | self._bl
        self._write_pcf(data | MASK_EN)   # EN high
        time.sleep_us(1)
        self._write_pcf(data & ~MASK_EN)  # EN low
        time.sleep_us(50)

    def _write_pcf(self, value):
        self._i2c.writeto(self._i2c_addr, bytes([value & 0xFF]))
