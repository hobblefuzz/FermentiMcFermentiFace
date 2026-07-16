"""Rotary encoder driver with bullet-proof Gray-code decoding.

Uses Ben Buxton's full-step state machine: counts exactly one step per
detent and rejects switch-bounce automatically (invalid transitions are
absorbed without producing a count).

    enc = RotaryEncoder(clk_pin=32, dt_pin=33, sw_pin=25)
    delta   = enc.get_delta()    # ± steps since last call
    pressed = enc.get_button()   # True once per press (edge-triggered)
"""

from machine import Pin
import time
import micropython


# ─────────────────────────────────────── state machine
# State IDs
_R_START      = const(0x00)
_R_CW_FINAL   = const(0x01)
_R_CW_BEGIN   = const(0x02)
_R_CW_NEXT    = const(0x03)
_R_CCW_BEGIN  = const(0x04)
_R_CCW_FINAL  = const(0x05)
_R_CCW_NEXT   = const(0x06)

# Direction flags OR'd into transition output
_DIR_CW       = const(0x10)
_DIR_CCW      = const(0x20)

# Debounce windows — module-level so they're optimised by the const compiler
_SW_DEBOUNCE_US = const(80_000)    # 80 ms — push-button (with 100nF hardware filter)

# Transition table: _TTABLE[current_state][pin_state]
# pin_state = (clk << 1) | dt          (both pins idle-high, so 0b11 = detent)
# Returned value = next_state | optional direction flag
_TTABLE = (
    # R_START (resting at detent — pin_state = 0b11)
    (_R_START,    _R_CW_BEGIN,   _R_CCW_BEGIN,  _R_START),
    # R_CW_FINAL
    (_R_CW_NEXT,  _R_START,      _R_CW_FINAL,   _R_START | _DIR_CW),
    # R_CW_BEGIN
    (_R_CW_NEXT,  _R_CW_BEGIN,   _R_START,      _R_START),
    # R_CW_NEXT
    (_R_CW_NEXT,  _R_CW_BEGIN,   _R_CW_FINAL,   _R_START),
    # R_CCW_BEGIN
    (_R_CCW_NEXT, _R_START,      _R_CCW_BEGIN,  _R_START),
    # R_CCW_FINAL
    (_R_CCW_NEXT, _R_CCW_FINAL,  _R_START,      _R_START | _DIR_CCW),
    # R_CCW_NEXT
    (_R_CCW_NEXT, _R_CCW_FINAL,  _R_CCW_BEGIN,  _R_START),
)


# ─────────────────────────────────────── encoder class
class RotaryEncoder:
    def __init__(self, clk_pin: int, dt_pin: int, sw_pin: int,
                 sw_active_low: bool = True):
        self._delta   = 0
        self._pressed = False
        self._state   = _R_START
        self._last_sw_us = 0

        self._clk = Pin(clk_pin, Pin.IN, Pin.PULL_UP)
        self._dt  = Pin(dt_pin,  Pin.IN, Pin.PULL_UP)
        self._sw  = Pin(sw_pin,  Pin.IN, Pin.PULL_UP)

        # Both rotary lines feed the same handler on both edges — the state
        # machine handles ordering and bounce rejection internally.
        edges = Pin.IRQ_RISING | Pin.IRQ_FALLING
        self._clk.irq(trigger=edges, handler=self._rotary_irq)
        self._dt.irq( trigger=edges, handler=self._rotary_irq)
        self._sw.irq(trigger=Pin.IRQ_FALLING if sw_active_low else Pin.IRQ_RISING,
                     handler=self._sw_irq)

    # ---------------------------------------------------------------- IRQs
    def _rotary_irq(self, pin):
        pin_state = (self._clk.value() << 1) | self._dt.value()
        new = _TTABLE[self._state][pin_state]
        self._state = new & 0x07
        if new & _DIR_CW:
            self._delta += 1
        elif new & _DIR_CCW:
            self._delta -= 1

    def _sw_irq(self, pin):
        # Re-read pin: real press holds SW low for ms, EMI spikes are <10us
        # so by the time the ISR runs, a noise pulse has already passed and
        # the pin reads HIGH again → reject.
        if pin.value() != 0:
            return
        now = time.ticks_us()
        # ticks_us wraps every ~17 min on the ESP32 — ticks_diff is only
        # well-defined for elapsed times under half that (~9 min).  When
        # _last_sw_us is older the diff becomes negative noise; we then
        # treat it as "definitely long ago" and accept the press.
        diff = time.ticks_diff(now, self._last_sw_us)
        if 0 <= diff < _SW_DEBOUNCE_US:
            return
        self._last_sw_us = now
        self._pressed = True

    # ------------------------------------------------------ main-loop API
    def get_delta(self) -> int:
        d = self._delta
        self._delta = 0
        return d

    def get_button(self) -> bool:
        if self._pressed:
            self._pressed = False
            return True
        return False

    def peek_delta(self) -> int:
        return self._delta
