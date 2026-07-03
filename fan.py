"""PWM fan controller.

Usage:
    fan = Fan(pin=26, freq=25_000)
    fan.set_percent(75)   # 75 % duty
    fan.off()
    fan.on()              # restore last non-zero duty
"""

from machine import Pin, PWM


class Fan:
    def __init__(self, pin: int, freq: int = 25_000):
        # Drive the pin LOW first to keep the transistor base/gate quiet.
        # Internal pulldown stays as a safety net even after PWM takes over.
        p = Pin(pin, Pin.OUT, Pin.PULL_DOWN, value=0)
        self._pwm        = PWM(p, freq=freq, duty=0)
        self._last_duty  = 0   # last non-zero duty (0-1023)
        self._running    = False

    # ---------------------------------------------------------------- API
    def set_percent(self, percent: int):
        """Set fan speed 0-100 %. 0 turns the fan off."""
        percent = max(0, min(100, int(percent)))
        duty    = int(percent * 1023 / 100)
        self._pwm.duty(duty)
        self._running = duty > 0
        if duty > 0:
            self._last_duty = duty

    def off(self):
        self._pwm.duty(0)
        self._running = False

    def on(self, percent: int = None):
        """Turn on — optionally at a given percent, otherwise restore last value."""
        if percent is not None:
            self.set_percent(percent)
        else:
            duty = self._last_duty if self._last_duty > 0 else 512
            self._pwm.duty(duty)
            self._running = True

    @property
    def running(self) -> bool:
        return self._running

    @property
    def duty_percent(self) -> int:
        return round(self._pwm.duty() * 100 / 1023)

    def deinit(self):
        self._pwm.deinit()
