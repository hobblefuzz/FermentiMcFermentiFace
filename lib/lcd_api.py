# LCD API base class for character LCDs
# Dave Hylands (MIT License) — adapted for MicroPython
# https://github.com/dhylands/python_lcd

import time

# Commands
LCD_CLR   = 0x01
LCD_HOME  = 0x02
LCD_ENTRY_MODE  = 0x04
LCD_ON_CTRL     = 0x08
LCD_MOVE        = 0x10
LCD_FUNCTION    = 0x20
LCD_CGRAM       = 0x40
LCD_DDRAM       = 0x80

# LCD_ENTRY_MODE flags
LCD_ENTRY_RIGHT = 0x00
LCD_ENTRY_LEFT  = 0x02
LCD_ENTRY_SHIFT = 0x01

# LCD_ON_CTRL flags
LCD_ON_DISPLAY = 0x04
LCD_ON_CURSOR  = 0x02
LCD_ON_BLINK   = 0x01

# LCD_FUNCTION flags
LCD_FUNC_8BIT   = 0x10
LCD_FUNC_2LINES = 0x08
LCD_FUNC_10DOTS = 0x04


class LcdApi:
    def __init__(self, num_lines, num_columns):
        self.num_lines   = num_lines
        self.num_columns = num_columns
        self.cursor_x    = 0
        self.cursor_y    = 0
        self._backlight  = True
        self._display_off()
        self.clear()
        self._hal_write_command(LCD_ENTRY_MODE | LCD_ENTRY_LEFT)
        self._display_on()

    # ---------------------------------------------------------------- public
    def clear(self):
        self.cursor_x = 0
        self.cursor_y = 0
        self._hal_write_command(LCD_CLR)
        time.sleep_ms(2)

    def show_cursor(self):
        self._hal_write_command(LCD_ON_CTRL | LCD_ON_DISPLAY | LCD_ON_CURSOR)

    def hide_cursor(self):
        self._hal_write_command(LCD_ON_CTRL | LCD_ON_DISPLAY)

    def blink_cursor_on(self):
        self._hal_write_command(LCD_ON_CTRL | LCD_ON_DISPLAY | LCD_ON_CURSOR | LCD_ON_BLINK)

    def blink_cursor_off(self):
        self._hal_write_command(LCD_ON_CTRL | LCD_ON_DISPLAY | LCD_ON_CURSOR)

    def move_to(self, cursor_x, cursor_y):
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y
        addr = cursor_x & 0x3F
        if cursor_y & 1:
            addr += 0x40
        if cursor_y & 2:
            addr += self.num_columns
        self._hal_write_command(LCD_DDRAM | addr)

    def putchar(self, char):
        if char == '\n':
            if self.cursor_y < self.num_lines - 1:
                self.move_to(0, self.cursor_y + 1)
            return
        self._hal_write_data(ord(char))
        self.cursor_x += 1
        if self.cursor_x >= self.num_columns:
            self.cursor_x = 0
            if self.cursor_y < self.num_lines - 1:
                self.cursor_y += 1

    def putstr(self, string):
        for char in string:
            self.putchar(char)

    def custom_char(self, location, charmap):
        """Define custom character (location 0-7, charmap: 8 bytes)."""
        location &= 0x07
        self._hal_write_command(LCD_CGRAM | (location << 3))
        time.sleep_us(40)
        for i in range(8):
            self._hal_write_data(charmap[i])
            time.sleep_us(40)

    def backlight_on(self):
        self._backlight = True
        self._hal_backlight_on()

    def backlight_off(self):
        self._backlight = False
        self._hal_backlight_off()

    # ---------------------------------------------------------- private
    def _display_on(self):
        self._hal_write_command(LCD_ON_CTRL | LCD_ON_DISPLAY)

    def _display_off(self):
        self._hal_write_command(LCD_ON_CTRL)

    # ----------------------------------------- HAL stubs (override in subclass)
    def _hal_write_command(self, cmd):
        raise NotImplementedError

    def _hal_write_data(self, data):
        raise NotImplementedError

    def _hal_backlight_on(self):
        pass

    def _hal_backlight_off(self):
        pass
