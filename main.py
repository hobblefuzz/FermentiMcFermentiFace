"""Fermenter Ventilation Controller — main entry point.

Boot sequence
-------------
1. Initialise I2C, LCD, BME280, encoder, fans
2. Load config from NVS
3. Spawn main loop:
     • Every cfg.interval_s : read sensor → update controller
     • Every ~50 ms         : poll encoder/button → tick GUI

Deep-sleep and ESP-NOW hooks are stubbed here for future expansion.
"""

import gc
import time
from machine import I2C, Pin, WDT, soft_reset

import pins
from config      import Config
from sensor      import SensorManager
from core_sensor import CoreTempSensor
from encoder     import RotaryEncoder
from fan         import Fan
from heater      import Heater
from controller  import VentilationController
from gui         import GUI
from wifi        import WiFi
from netclient   import NetClient

# WiFi / Pi credentials live in secrets.py (gitignored). Missing → offline.
try:
    import secrets
except ImportError:
    class secrets:
        WIFI_SSID = ""
        WIFI_PASS = ""
        PI_HOST   = ""
        PI_PORT   = 8080

from lib.i2c_lcd import I2cLcd


# ─────────────────────────────────────── boot
def boot():
    print("[main] Fermenter Controller starting…")

    # FIRST thing: drive fan PWM pins low and the relay pin to its OFF level so
    # floating GPIOs don't switch the transistors / relay during the rest of
    # boot.  Heater() / Fan() take over later with the same safe state.
    Pin(pins.FAN1_PIN, Pin.OUT, Pin.PULL_DOWN, value=0)
    Pin(pins.FAN2_PIN, Pin.OUT, Pin.PULL_DOWN, value=0)
    Pin(pins.RELAY_PIN, Pin.OUT, value=(1 if pins.RELAY_ACTIVE_LOW else 0))

    # Shared hardware I2C bus
    i2c = I2C(0, sda=Pin(pins.I2C_SDA), scl=Pin(pins.I2C_SCL),
              freq=pins.I2C_FREQ)

    # Bus settle time after I2C creation
    time.sleep_ms(50)

    # BME280 first — call before any other I2C traffic.  i2c.scan() and
    # LCD writes both leave the ESP32 hardware I2C peripheral in a state
    # where the next readfrom_mem on the BME280 address NACKs reliably.
    try:
        sensor = SensorManager(i2c, addr=pins.BME280_I2C_ADDR)
        sensor.read()
        print(f"[main] BME280 ok: {sensor.temperature_c}°C  {sensor.humidity}%RH")
    except Exception as e:
        print(f"[main] BME280 error: {e}")
        sensor = _DummySensor()

    # Scan bus AFTER sensor init (purely informational)
    devices = i2c.scan()
    print(f"[main] I2C devices found: {[hex(d) for d in devices]}")

    # LCD
    lcd = I2cLcd(i2c, pins.LCD_I2C_ADDR, pins.LCD_LINES, pins.LCD_COLS)
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("Fermenter v1.0  ")
    lcd.move_to(0, 1)
    if not sensor.ok:
        lcd.putstr("Sensor missing! ")
    else:
        lcd.putstr("Initialising... ")
    time.sleep_ms(1200)

    # Config
    cfg = Config()

    # DS18B20 core-temperature sensor (1-Wire)
    core = CoreTempSensor(pins.ONEWIRE_PIN)
    print(f"[main] DS18B20 sensors found: {core.sensor_count}")

    # Fans + heating relay
    fan1   = Fan(pins.FAN1_PIN, freq=pins.FAN_PWM_FREQ)
    fan2   = Fan(pins.FAN2_PIN, freq=pins.FAN_PWM_FREQ)
    heater = Heater(pins.RELAY_PIN, active_low=pins.RELAY_ACTIVE_LOW,
                    min_cycle_s=cfg.relay_min_s)

    # Controller
    ctrl = VentilationController(cfg, fan1, fan2, heater)

    # Encoder
    enc = RotaryEncoder(pins.ENC_CLK, pins.ENC_DT, pins.ENC_SW)

    # GUI
    gui = GUI(lcd, cfg, ctrl, sensor, core)

    # WiFi + Pi client (blocking connect here, BEFORE the WDT is armed)
    wifi = WiFi(secrets.WIFI_SSID, secrets.WIFI_PASS)
    net  = NetClient(cfg, secrets.PI_HOST, secrets.PI_PORT)
    if cfg.wifi_en and secrets.WIFI_SSID:
        lcd.move_to(0, 1)
        lcd.putstr(_pad("WiFi connect... ", 16))
        if wifi.connect():
            print(f"[main] WiFi connected: {wifi.ip()}")
        else:
            print("[main] WiFi connect failed — running offline")

    # Hardware watchdog — only armed if enabled in config.  NOTE: once the
    # ESP32 WDT is created it cannot be stopped; toggling it off in the menu
    # takes effect only after a reboot.
    wdt = None
    if cfg.wdt_enabled:
        wdt = WDT(timeout=8000)
        print("[main] Watchdog armed (8 s)")

    print("[main] Boot complete — entering main loop")
    _main_loop(cfg, sensor, core, ctrl, enc, gui, wdt, wifi, net,
               fan1, fan2)


def _pad(s, w):
    s = s[:w]
    return s + " " * (w - len(s)) if len(s) < w else s


# ─────────────────────────────────────── main loop
def _main_loop(cfg, sensor, core, ctrl, enc, gui, wdt, wifi, net, fan1, fan2):
    TICK_MS      = 50       # GUI/encoder poll interval
    GC_PERIOD_MS = 60_000   # explicit gc.collect every minute
    last_sense    = time.ticks_ms() - cfg.interval_s * 1000  # measure immediately
    last_gc       = time.ticks_ms()
    last_wifi     = time.ticks_ms()
    awaiting_core = False    # True while a DS18B20 conversion is in flight

    while True:
        now = time.ticks_ms()

        # ── Sensing + control, guarded so a transient I2C/1-Wire glitch
        #    (e.g. relay-switching EMI) can't kill the main loop.
        try:
            # Start a sensing cycle on interval (BME280 instant, DS18B20 async)
            if not awaiting_core and time.ticks_diff(now, last_sense) >= cfg.interval_s * 1000:
                last_sense = now
                sensor.read()
                if core.start():
                    awaiting_core = True       # finish once conversion is ready
                else:
                    # conversion couldn't start — apply grace, don't hard-fault
                    chamber = sensor.temperature_c if sensor.ok else None
                    ctrl.update(core.on_cycle_fail(), chamber, sensor.humidity)

            # Finish the cycle when the DS18B20 conversion is done
            if awaiting_core and core.ready():
                awaiting_core = False
                chamber = sensor.temperature_c if sensor.ok else None
                ctrl.update(core.read(), chamber, sensor.humidity)
        except Exception as e:
            print(f"[loop] sensing/control error: {e}")
            awaiting_core = False

        # ── Encoder delta — feed each detent step into the GUI
        delta = enc.get_delta()
        for _ in range(abs(delta)):
            gui.on_encoder(1 if delta > 0 else -1)

        # ── Button press
        if enc.get_button():
            gui.on_button()

        # ── GUI render tick
        gui.tick()

        # ── Networking: restore local values if external control was turned
        #    off; otherwise poll the Pi when connected.  A poll blocks at most
        #    the socket timeout (4 s) < watchdog window (8 s).
        net.sync_mode()
        if cfg.wifi_en:
            if wifi.connected:
                if net.due():
                    net.poll({
                        "id": "fermenter",
                        "core": core.temperature_c,
                        "chamber": sensor.temperature_c if sensor.ok else None,
                        "rh": sensor.humidity,
                        "heater": ctrl.heater_on,
                        "fan": ctrl.status_str(),
                        "fan1": fan1.duty_percent,
                        "fan2": fan2.duty_percent,
                        "core_fault": ctrl.core_fault,
                        "chamber_alarm": ctrl.chamber_alarm,
                        "ext": cfg.ext_ctrl,
                    })
            elif time.ticks_diff(now, last_wifi) >= 15000:
                last_wifi = now
                wifi.ensure()          # non-blocking reconnect nudge

        # ── Feed the watchdog (only if armed)
        if wdt:
            wdt.feed()

        # ── Periodic GC to defragment the heap on long-running boots
        if time.ticks_diff(time.ticks_ms(), last_gc) >= GC_PERIOD_MS:
            gc.collect()
            last_gc = time.ticks_ms()

        # ── Sleep remainder of tick budget
        elapsed = time.ticks_diff(time.ticks_ms(), now)
        if elapsed < TICK_MS:
            time.sleep_ms(TICK_MS - elapsed)


# ─────────────────────────────────────── fallback sensor
class _DummySensor:
    """Used when BME280 is not present (e.g. wiring issue)."""
    temperature_c : float = 25.0
    temperature_f : float = 77.0
    humidity      : float = 50.0
    pressure_hpa  : float = 1013.0
    ok            : bool  = False

    def read(self):
        return self.temperature_c, self.temperature_f, self.humidity, self.pressure_hpa

    def temperature(self, fahrenheit=False):
        return self.temperature_f if fahrenheit else self.temperature_c


# ─────────────────────────────────────── entry
boot()
