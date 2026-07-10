"""1602 LCD GUI with hierarchical menu system.

Architecture
------------
GUI            — top-level manager (timeout, blink timer, wake/sleep)
 ├── HomeScreen  — live status display
 └── MenuScreen  — scrollable list of MenuItem objects
      └── EditScreen  — inline value editor (replaces line 2)

Navigation
----------
Encoder rotate : scroll items / change value
Button press   : enter item / confirm edit / go back (long-press from root)

All screens return a *transition* constant from handle_encoder / handle_button.
The GUI manager interprets the transition and swaps screens accordingly.
"""

import time
from machine import Timer


# ─────────────────────────────────────── transition constants
class T:
    NONE      = 0   # stay on current screen
    HOME      = 1   # go back to home screen
    BACK      = 2   # go up one menu level
    ENTER     = 3   # enter selected item (set by caller)
    REFRESH   = 4   # re-render without changing state


# ─────────────────────────────────────── menu data definition
# Each entry: (display_label, config_key, type, min, max, unit_str, step)
# type: "bool" | "int" | "reset"
# unit_str: appended to value on display  e.g. "%" "%RH" "s" "min" "C" "F"

def _build_menu_tree():
    GLOBAL = [
        ("Toggle Ctrl",  "ctrl_enabled",    "bool",  0,    1,  "",    1),
        ("Unit C/F",     "use_fahrenheit",  "bool",  0,    1,  "",    1),
        ("Interval",     "interval_s",      "int",   1,   60,  "s",   1),
        ("Sleep",        "scr_timeout_s","int",   5,  120,  "s",   5),
        ("Always On",    "scr_always_on","bool",  0,    1,  "",    1),
        ("Chamber Max",  "chamber_max","int",  30,   80,  "C",   1),
        ("Watchdog",     "wdt_enabled",     "bool",  0,    1,  "",    1),
        ("WiFi",         "wifi_en",         "bool",  0,    1,  "",    1),
        ("Ext ctrl",     "ext_ctrl",        "bool",  0,    1,  "",    1),
        ("< Back",       "back"),
    ]
    RH = [
        ("Toggle RH",    "rh_enabled",      "bool",  0,   1,   "",    1),
        ("Upper limit",  "rh_upper",        "int",   0, 100,   "%",   1),
        ("Lower limit",  "rh_lower",        "int",   0, 100,   "%",   1),
        ("PWM Fan1",     "rh_pwm_f1",       "int",   0, 100,   "%",   1),
        ("PWM Fan2",     "rh_pwm_f2",       "int",   0, 100,   "%",   1),
        ("< Back",       "back"),
    ]
    # Merged heating + cooling, both on the core (DS18B20) temperature.
    # Temperatures step in 0.1 °C (step 1 on the x10 keys).
    TEMP = [
        ("Heating",      "heat_enabled",    "bool",  0,    1,  "",    1),
        ("Target",       "target_x10",      "int",   0,  800,  "C",   1),  # core setpoint
        ("Core dT",      "dt_lower_x10",    "int",   1,   50,  "C",   1),  # heat below target−dT
        ("Floor dT",     "dt_floor_x10",    "int",   0,  100,  "C",   1),  # heat if chamber ≤ target−dT
        ("Ceil dT",      "dt_ceil_x10",     "int",   1,  100,  "C",   1),  # brake: block if chamber ≥ target+dT
        ("Relay min",    "relay_min_s",     "int",  10,  600,  "s",   5),
        ("Cooling",      "temp_enabled",    "bool",  0,    1,  "",    1),
        ("Cool on",      "temp_upper_x10",  "int",   0,  800,  "C",   1),  # core above → fans
        ("Cool off",     "temp_lower_x10",  "int",   0,  800,  "C",   1),  # core below → stop
        ("Cool PWM1",    "temp_pwm_f1",     "int",   0,  100,  "%",   1),
        ("Cool PWM2",    "temp_pwm_f2",     "int",   0,  100,  "%",   1),
        ("< Back",       "back"),
    ]
    IDLE = [
        ("Toggle IDLE",  "idle_enabled",    "bool",  0,   1,   "",    1),
        ("Max idle",     "idle_max_idle",   "int",   2,  60,   "min", 1),
        ("Duration",     "idle_duration",   "int",   5, 120,   "s",   1),
        ("PWM Fan1",     "idle_pwm_f1",     "int",   0, 100,   "%",   1),
        ("PWM Fan2",     "idle_pwm_f2",     "int",   0, 100,   "%",   1),
        ("< Back",       "back"),
    ]
    MAIN = [
        ("Global Ctrl",  GLOBAL),
        ("Temperature",  TEMP),
        ("RH-Control",   RH),
        ("IDLE-Control", IDLE),
        ("Factory Reset", "reset"),
        ("< Home",       "home"),
    ]
    return MAIN


MENU_TREE = _build_menu_tree()


# x10 temperature keys that are DELTAS (differences), not absolute temperatures.
# They convert to °F by scaling only (×9/5), without the +32 offset.
_DELTA_KEYS = frozenset(("dt_lower_x10", "dt_floor_x10", "dt_ceil_x10"))


# ─────────────────────────────────────── helpers
def _fmt_value(key, raw_val, use_fahrenheit=False):
    """Format a raw config value for display (max ~8 chars)."""
    # Bool keys → ON / OFF
    if key.endswith("_enabled") or key in ("ctrl_enabled", "use_fahrenheit",
                                            "scr_always_on", "wifi_en", "ext_ctrl"):
        return "ON " if raw_val else "OFF"
    # Temperature stored as x10 — convert to degrees
    if key.endswith("_x10"):
        c = raw_val / 10.0
        if use_fahrenheit:
            f = c * 9 / 5 if key in _DELTA_KEYS else c * 9 / 5 + 32
            return f"{f:.1f}F"
        return f"{c:.1f}C"
    return str(raw_val)


def _pad(s: str, width: int) -> str:
    """Pad / truncate string to exactly *width* characters (MicroPython has no str.ljust)."""
    s = s[:width]
    if len(s) < width:
        s = s + " " * (width - len(s))
    return s


# ─────────────────────────────────────── HomeScreen
class HomeScreen:
    def __init__(self, config, controller, sensor, core):
        self._cfg  = config
        self._ctrl = controller
        self._sens = sensor
        self._core = core
        self._line1 = ""
        self._line2 = ""

    def invalidate(self):
        """Force full redraw on next render (after lcd.clear())."""
        self._line1 = ""
        self._line2 = ""

    def _conv(self, c):
        """Convert °C to the configured display unit."""
        return c * 9 / 5 + 32 if self._cfg.use_fahrenheit else c

    def render(self, lcd, blink_state: bool):
        cfg  = self._cfg
        ctrl = self._ctrl
        sens = self._sens
        core = self._core
        unit = "F" if cfg.use_fahrenheit else "C"

        rh        = sens.humidity
        rh_str    = f"{rh:.0f}%" if sens.ok else "--%"
        core_str  = f"{self._conv(core.temperature_c):.1f}{unit}" if core.ok else f"--.-{unit}"
        cham_str  = f"{self._conv(sens.temperature_c):.0f}{unit}" if sens.ok else f"--{unit}"
        heat_mark = "H+" if ctrl.heater_on else "H-"

        # ── Line 1: alarms take priority (blinking), else core temp + RH
        core_fault_relevant = ctrl.core_fault and (cfg.heat_enabled or cfg.temp_enabled)
        if ctrl.chamber_alarm:
            line1 = "** OVERTEMP **" if blink_state else ""
        elif core_fault_relevant and blink_state:
            line1 = "!CORE SENSOR!"
        else:
            line1 = f"Core:{core_str} {rh_str}"
        line1 = _pad(line1, 16)

        # ── Line 2: heat marker + fan status + chamber temp
        line2 = _pad(f"{heat_mark} Fan:{ctrl.status_str()} {cham_str}", 16)

        if line1 != self._line1:
            lcd.move_to(0, 0)
            lcd.putstr(line1)
            self._line1 = line1
        if line2 != self._line2:
            lcd.move_to(0, 1)
            lcd.putstr(line2)
            self._line2 = line2

    def handle_encoder(self, delta: int) -> int:
        return T.NONE   # encoder does nothing on home screen

    def handle_button(self) -> int:
        return T.ENTER  # → main menu


# ─────────────────────────────────────── EditScreen (inline editor)
class EditScreen:
    """Replaces line 2 of the parent menu to edit a single value."""

    def __init__(self, config, item_def, parent_menu):
        self._cfg    = config
        self._def    = item_def   # (label, key, type, min, max, unit, step)
        self._parent = parent_menu
        label, key, typ, vmin, vmax, unit, step = item_def
        self._key   = key
        self._type  = typ
        self._min   = vmin
        self._max   = vmax
        self._unit  = unit
        self._step  = step
        self._label = label
        self._val   = getattr(config, key)
        self._dirty = False

    # Label shown on line 1 is the parent's responsibility.
    def render_line2(self, lcd, blink_state: bool):
        """Write the edit field to LCD row 1 (0-indexed)."""
        val    = self._val
        use_f  = self._cfg.use_fahrenheit
        v_str  = _fmt_value(self._key, val, use_f)
        cursor = ">" if blink_state else " "
        # Format: "< OFF >"  or  "< 65% >"
        unit_s = self._unit
        if self._key.endswith("_x10"):
            unit_s = "F" if use_f else "C"
        content = f"{cursor}{v_str}{unit_s}{cursor}"
        lcd.move_to(0, 1)
        lcd.putstr(_pad(content, 16))

    def handle_encoder(self, delta: int) -> int:
        if self._type == "bool":
            self._val = 0 if self._val else 1
        else:
            self._val = max(self._min, min(self._max, self._val + delta * self._step))
        self._dirty = True
        return T.REFRESH

    def handle_button(self) -> int:
        """Save and return to parent menu, enforcing lower ≤ upper constraints."""
        if self._dirty:
            val = self._val
            # Enforce paired lower/upper limits
            key = self._key
            cfg = self._cfg
            if key.endswith("_lower") or key.endswith("_lower_x10"):
                upper_key = key.replace("_lower", "_upper")
                try:                       # standalone deltas (dt_lower) have no pair
                    val = min(val, getattr(cfg, upper_key))
                except AttributeError:
                    pass
            elif key.endswith("_upper") or key.endswith("_upper_x10"):
                lower_key = key.replace("_upper", "_lower")
                try:
                    val = max(val, getattr(cfg, lower_key))
                except AttributeError:
                    pass

            # Cross-constraint: the heating target must stay below the cooling
            # band so heater and fans never fight (deadband).  target ≤ temp_lower.
            if key == "target_x10":
                val = min(val, cfg.temp_lower_x10)
            elif key == "temp_lower_x10":
                val = max(val, cfg.target_x10)

            # (The three deltas are unconstrained differences; the runtime safety
            # gate "no heating once core ≥ target" is the real protection.)

            setattr(cfg, key, val)
        return T.BACK


# ─────────────────────────────────────── ConfirmScreen
class ConfirmScreen:
    """Yes / No confirmation (used for factory reset)."""

    def __init__(self, config):
        self._cfg      = config
        self._selected = 0   # 0=YES, 1=NO

    def render(self, lcd, blink_state: bool):
        lcd.move_to(0, 0)
        lcd.putstr(_pad("Are you sure?", 16))
        yes = (">YES" if self._selected == 0 else " YES") if (blink_state or self._selected != 0) else "    "
        no  = (">NO " if self._selected == 1 else " NO ") if (blink_state or self._selected != 1) else "    "
        lcd.move_to(0, 1)
        lcd.putstr(_pad(f"{yes}  {no}", 16))

    def handle_encoder(self, delta: int) -> int:
        self._selected = (self._selected + delta) % 2
        return T.REFRESH

    def handle_button(self) -> int:
        if self._selected == 0:
            self._cfg.reset()
        return T.HOME


# ─────────────────────────────────────── MenuScreen
class MenuScreen:
    """Scrollable 2-line menu.  Line 0 = selected (with cursor), line 1 = next."""

    def __init__(self, config, items, title="", parent=None):
        self._cfg    = config
        self._items  = items   # list of (label, submenu_or_key) tuples or item_defs
        self._title  = title
        self._parent = parent
        self._idx    = 0       # currently selected index
        self._editor = None    # active EditScreen (if any)
        self._sub    = None    # active sub-MenuScreen (if any)
        self._prev_l1 = ""
        self._prev_l2 = ""

    # ---------------------------------------------------------------- render
    def render(self, lcd, blink_state: bool):
        if self._sub is not None:
            self._sub.render(lcd, blink_state)
            return
        if self._editor is not None:
            # Line 1 stays as current item label; editor owns line 2
            label = self._current_label()
            l1 = _pad(label, 16)
            if l1 != self._prev_l1:
                lcd.move_to(0, 0)
                lcd.putstr(l1)
                self._prev_l1 = l1
            self._editor.render_line2(lcd, blink_state)
            return

        cursor = ">" if blink_state else " "
        l1 = _pad(f"{cursor}{self._current_label()}", 16)
        next_label = self._label_at(self._idx + 1)
        l2 = _pad(f" {next_label}" if next_label else " " * 15, 16)

        if l1 != self._prev_l1:
            lcd.move_to(0, 0)
            lcd.putstr(l1)
            self._prev_l1 = l1
        if l2 != self._prev_l2:
            lcd.move_to(0, 1)
            lcd.putstr(l2)
            self._prev_l2 = l2

    # -------------------------------------------------------- encoder / button
    def handle_encoder(self, delta: int) -> int:
        if self._sub is not None:
            result = self._sub.handle_encoder(delta)
            return self._route(result, from_sub=True)
        if self._editor is not None:
            result = self._editor.handle_encoder(delta)
            return self._route(result, from_editor=True)
        # Scroll menu
        self._idx = (self._idx + delta) % len(self._items)
        self._prev_l1 = self._prev_l2 = ""   # force redraw
        return T.REFRESH

    def handle_button(self) -> int:
        if self._sub is not None:
            result = self._sub.handle_button()
            return self._route(result, from_sub=True)
        if self._editor is not None:
            result = self._editor.handle_button()
            return self._route(result, from_editor=True)

        item = self._items[self._idx]

        # Main-menu style entry: (label, submenu_list_or_sentinel_string)
        if len(item) == 2:
            label, payload = item
            if payload == "back":
                return T.BACK
            if payload == "home":
                return T.HOME
            if payload == "reset":
                self._sub = ConfirmScreen(self._cfg)
                self._invalidate()
                return T.REFRESH
            if isinstance(payload, list):
                self._sub = MenuScreen(self._cfg, payload, title=label, parent=self)
                self._invalidate()
                return T.REFRESH

        # Item-def entry: (label, key, type, min, max, unit, step)
        if len(item) == 7:
            self._editor = EditScreen(self._cfg, item, self)
            self._invalidate()
            return T.REFRESH

        return T.BACK  # fallback

    # ---------------------------------------------------------------- helpers
    def _route(self, result, from_sub=False, from_editor=False) -> int:
        if result == T.BACK:
            if from_sub:
                self._sub = None
            if from_editor:
                self._editor = None
            self._invalidate()
            return T.REFRESH
        if result == T.HOME:
            self._sub = None
            self._editor = None
            return T.HOME
        return result

    def _current_label(self) -> str:
        return self._item_label(self._items[self._idx])

    def _label_at(self, idx: int):
        if idx >= len(self._items):
            return None
        return self._item_label(self._items[idx])

    def _item_label(self, item) -> str:
        if len(item) == 2:
            return item[0]
        # item_def — append current value
        label, key, typ, *_ = item
        raw = getattr(self._cfg, key)
        v   = _fmt_value(key, raw, self._cfg.use_fahrenheit)
        short_label = label[:9]
        return _pad(f"{short_label}:{v}", 15)

    def _invalidate(self):
        self._prev_l1 = self._prev_l2 = ""


# ─────────────────────────────────────── GUI manager
class GUI:
    BLINK_PERIOD_MS   = 500       # cursor blink period

    def __init__(self, lcd, config, controller, sensor, core):
        self._lcd    = lcd
        self._cfg    = config
        self._ctrl   = controller
        self._sens   = sensor
        self._core   = core

        self._home   = HomeScreen(config, controller, sensor, core)
        self._menu   = None    # created on demand
        self._screen = self._home   # current top-level screen

        self._active          = True
        self._blink_state     = True
        self._last_activity   = time.ticks_ms()
        self._last_blink_tick = time.ticks_ms()
        self._needs_clear     = False

        lcd.backlight_on()
        lcd.clear()

    # ----------------------------------------------------------------- tick
    def tick(self):
        """Call from the main loop as fast as practical (e.g. every 50 ms)."""
        now = time.ticks_ms()

        # Screen timeout (configurable, in seconds) — skipped if always-on
        if not self._cfg.scr_always_on:
            if time.ticks_diff(now, self._last_activity) > self._cfg.scr_timeout_s * 1000:
                if self._active:
                    self._sleep()
                return
        elif not self._active:
            # always_on was just turned on while screen was sleeping → wake immediately
            self._wake()

        # Blink toggle
        if time.ticks_diff(now, self._last_blink_tick) >= self.BLINK_PERIOD_MS:
            self._blink_state     = not self._blink_state
            self._last_blink_tick = now

        # Render with I2C-error tolerance: a stray NACK shouldn't crash the
        # main loop.  On error, force a full redraw on the next tick so
        # the cached "already drawn" state doesn't keep us stale.
        try:
            if self._needs_clear:
                self._lcd.clear()
                self._needs_clear = False
            self._screen.render(self._lcd, self._blink_state)
        except OSError as e:
            print(f"[GUI] LCD write error: {e} — re-rendering next tick")
            self._needs_clear = True
            if hasattr(self._screen, "invalidate"):
                self._screen.invalidate()
            elif hasattr(self._screen, "_invalidate"):
                self._screen._invalidate()

    # -------------------------------------------------- encoder / button
    def on_encoder(self, delta: int):
        if not self._active:
            self._wake()
            return
        self._poke()
        result = self._screen.handle_encoder(delta)
        self._handle_transition(result)

    def on_button(self):
        if not self._active:
            self._wake()
            return
        self._poke()
        result = self._screen.handle_button()
        self._handle_transition(result)

    # ----------------------------------------------------------- transitions
    def _handle_transition(self, result: int):
        if result in (T.HOME, T.BACK) and self._screen is not self._home:
            # T.BACK from the root MenuScreen means "leave the menu entirely"
            self._go_home()
        elif result == T.ENTER and self._screen is self._home:
            self._go_menu()

    def _go_home(self):
        self._menu = None
        self._screen = self._home
        self._home.invalidate()       # cache must be cleared after lcd.clear()
        self._needs_clear = True

    def _go_menu(self):
        self._menu   = MenuScreen(self._cfg, MENU_TREE, title="Main Menu")
        self._screen = self._menu
        self._needs_clear = True

    # ----------------------------------------------------------------- sleep
    def _sleep(self):
        self._active = False
        self._lcd.backlight_off()

    def _wake(self):
        self._lcd.backlight_on()
        self._active = True
        self._poke()
        self._go_home()
        self._needs_clear = True

    def _poke(self):
        self._last_activity = time.ticks_ms()
