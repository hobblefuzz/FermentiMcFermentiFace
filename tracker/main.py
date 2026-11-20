"""Temperature tracker (separate ESP32).  Sensor-only — it controls nothing.

Two modes (ALWAYS_ON below):

* Always-on (default, for USB / mains power): stays awake, sends a short
  heartbeat to the Pi every PING_S seconds and a reading every interval.  The
  interval is set in the dashboard and arrives in the reply to every heartbeat,
  so a change takes effect within seconds; the dashboard can also ask for a
  reading right now.
* Deep sleep (battery): wake, read, POST /api/track, sleep for the interval the
  Pi sent back (INTERVAL_S if it couldn't be reached).

Fast wake tips for the battery mode (configure in secrets.py): static IP
(skips DHCP), BSSID (skips the channel scan), and use the Pi's IP (no DNS).
"""

import time
import socket
import network
import machine
from machine import Pin
import onewire
import ds18x20
import ujson

# WiFi / Pi credentials + fast-connect options
try:
    import secrets
except ImportError:                       # allow flashing without secrets yet
    class secrets:
        WIFI_SSID = ""; WIFI_PASS = ""
        PI_HOST = "192.168.1.50"; PI_PORT = 8080
        STATIC_IP = None                  # (ip, subnet, gateway, dns) or None
        BSSID = None                      # AP MAC as bytes, or None

# ─────────────────────────────────────── config
DEVICE_ID     = "moromi"           # shows up as its own stream in the dashboard
ALWAYS_ON     = True         # True: USB/mains, stay awake.  False: battery, deep sleep
INTERVAL_S    = 30 * 60      # reading interval until the Pi sends one (dashboard setting)
ONEWIRE_PIN   = 15           # DS18B20 data (4.7 kΩ pull-up to 3.3 V) — D15 on the board
WIFI_TIMEOUT  = 20           # s to wait for a connection

# Always-on mode only
PING_S        = 10           # heartbeat to the Pi — fetches settings / "measure now"
OFFLINE_RESET_S = 10 * 60    # reboot after this long without reaching the Pi
WDT_S         = 60           # hardware watchdog (reboots a hung node); None disables

# Button: momentary switch from WAKE_PIN to GND.  Deep sleep: ends the sleep
# and takes a reading.  Always-on: takes a reading right away.  Must be an RTC-capable GPIO (0, 2, 4, 12-15, 25-27, 32-39) — GPIO34-39 are
# input-only with no internal pull-ups, so they'd need an external one.
# Set to None to disable.
WAKE_PIN      = 33

# Battery sense (optional). Set BATT_ADC_PIN to an ADC1 pin (32-39) and wire a
# divider from the LiPo; leave None to disable. BATT_DIVIDER = Vbatt/Vpin.
# On USB there is no battery to watch — leave it None there.
BATT_ADC_PIN  = None
BATT_DIVIDER  = 2.0

POWER = "usb" if ALWAYS_ON else "batt"   # tells the dashboard which tiles to show


# ─────────────────────────────────────── helpers
def read_temp():
    try:
        ds = ds18x20.DS18X20(onewire.OneWire(Pin(ONEWIRE_PIN)))
        roms = ds.scan()
        if not roms:
            return None
        ds.convert_temp()
        time.sleep_ms(750)
        t = ds.read_temp(roms[0])
        if t is None or t <= -55 or t >= 125 or abs(t) < 0.01:
            return None
        return round(t, 2)
    except Exception as e:
        print("[track] sensor error:", e)
        return None


def read_batt():
    if BATT_ADC_PIN is None:
        return None
    try:
        from machine import ADC
        adc = ADC(Pin(BATT_ADC_PIN))
        adc.atten(ADC.ATTN_11DB)          # full ~0-3.3 V range
        try:
            v_pin = sum(adc.read_uv() for _ in range(16)) / 16 / 1e6   # calibrated
        except AttributeError:
            v_pin = sum(adc.read() for _ in range(16)) / 16 / 4095 * 3.3
        return round(v_pin * BATT_DIVIDER, 3)
    except Exception as e:
        print("[track] batt error:", e)
        return None


def connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    if getattr(secrets, "STATIC_IP", None):
        try:
            wlan.ifconfig(secrets.STATIC_IP)   # static → no DHCP round-trip
        except Exception:
            pass
    bssid = getattr(secrets, "BSSID", None)
    t0 = time.ticks_ms()
    try:
        if bssid:
            wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS, bssid=bssid)
        else:
            wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
    except TypeError:                          # build without bssid kwarg
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
    except OSError as e:                       # e.g. "Wifi Internal Error" mid-reconnect
        print("[track] WiFi connect error:", e)
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > WIFI_TIMEOUT * 1000:
            print("[track] WiFi timeout after", WIFI_TIMEOUT, "s")
            return False
        time.sleep_ms(100)
    try:                                       # diagnostics: connect time + signal
        print("[track] WiFi up in {} ms, RSSI {} dBm".format(
            time.ticks_diff(time.ticks_ms(), t0), wlan.status("rssi")))
    except Exception:
        pass
    return True


def wifi_kick():
    """Power-cycle the radio — clears the stuck states a plain reconnect can't."""
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(False)
        time.sleep_ms(500)
        wlan.active(True)
    except Exception as e:
        print("[track] WiFi kick failed:", e)


def parse_reply(raw):
    """JSON body of a raw HTTP response, or {} if there is none / it's garbage."""
    i = raw.find(b"\r\n\r\n")
    if i < 0:
        return {}
    try:
        d = ujson.loads(raw[i + 4:])
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def post(path, payload, timeout=4):
    """POST JSON to the Pi and return its JSON reply (raises on network errors)."""
    body = ujson.dumps(payload)
    addr = socket.getaddrinfo(secrets.PI_HOST, secrets.PI_PORT)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(addr)
        req = ("POST {} HTTP/1.0\r\nHost: {}\r\n"
               "Content-Type: application/json\r\nContent-Length: {}\r\n"
               "Connection: close\r\n\r\n{}").format(path, secrets.PI_HOST, len(body), body)
        s.send(req.encode())
        raw = b""
        while len(raw) < 2048:                 # HTTP/1.0: server closes when done
            chunk = s.recv(512)
            if not chunk:
                break
            raw += chunk
    finally:
        s.close()
    return parse_reply(raw)


def clamp_interval(v, fallback):
    try:
        return max(10, min(24 * 3600, int(v)))
    except Exception:
        return fallback


def reading():
    return {"id": DEVICE_ID, "temp": read_temp(), "batt": read_batt(), "power": POWER}


# ─────────────────────────────────────── button
_EXT0 = getattr(machine, "EXT0_WAKE", getattr(machine, "PIN_WAKE", None))


def woken_by_button():
    # The button is an EXT0 source, so a press still shows up as a
    # DEEPSLEEP_RESET — only wake_reason() tells the two apart.  The constant is
    # named EXT0_WAKE on most builds and PIN_WAKE on some, hence the lookup.
    if WAKE_PIN is None or _EXT0 is None:
        return False
    try:
        return machine.reset_cause() == machine.DEEPSLEEP_RESET and \
               machine.wake_reason() == _EXT0
    except Exception:
        return False


def arm_wake_button():
    """Let a press on WAKE_PIN (to GND) end the deep sleep early."""
    if WAKE_PIN is None:
        return
    try:
        import esp32
        btn = Pin(WAKE_PIN, Pin.IN, Pin.PULL_UP)
        esp32.wake_on_ext0(pin=btn, level=esp32.WAKEUP_ALL_LOW)
    except Exception as e:
        print("[track] wake button not armed:", e)


# ─────────────────────────────────────── battery mode: one reading, deep sleep
def run_once():
    payload = reading()
    interval = INTERVAL_S
    if connect():
        try:
            reply = post("/api/track", payload)
            interval = clamp_interval(reply.get("interval_s"), INTERVAL_S)
            print("[track] sent:", payload)
        except Exception as e:
            print("[track] post failed:", e)
    else:
        print("[track] no WiFi")
    arm_wake_button()
    print("[track] deep sleep for", interval, "s (press the button to cut it short)")
    machine.deepsleep(interval * 1000)


# ─────────────────────────────────────── always-on mode
def run_forever():
    wdt = None
    if WDT_S:
        try:
            wdt = machine.WDT(timeout=WDT_S * 1000)
        except Exception as e:
            print("[track] no watchdog:", e)

    def feed():
        if wdt:
            wdt.feed()

    btn = None
    if WAKE_PIN is not None:
        try:
            btn = Pin(WAKE_PIN, Pin.IN, Pin.PULL_UP)
        except Exception:
            btn = None

    interval = INTERVAL_S
    now = time.ticks_ms()
    last_read = None                  # ticks of the last successful reading
    read_due = True                   # first reading right after boot
    next_ping = now
    last_contact = now
    last_btn = now
    hold_until = now                  # after a failure, wait a heartbeat before retrying
    wifi_fails = 0

    while True:
        feed()
        now = time.ticks_ms()

        # Button → reading now (debounced: at most one per 2 s)
        if btn is not None and btn.value() == 0 and time.ticks_diff(now, last_btn) > 2000:
            last_btn = now
            read_due = True
            print("[track] button → measuring now")

        if last_read is not None and time.ticks_diff(now, last_read) >= interval * 1000:
            read_due = True

        if (read_due or time.ticks_diff(now, next_ping) >= 0) and \
                time.ticks_diff(now, hold_until) >= 0:
            ok = False
            if connect():
                wifi_fails = 0
                feed()
                try:
                    if read_due:
                        payload = reading()
                        payload["interval_s"] = interval
                        feed()
                        reply = post("/api/track", payload)
                        last_read = time.ticks_ms()
                        read_due = False
                        print("[track] sent:", payload)
                    else:
                        reply = post("/api/track/ping", {"id": DEVICE_ID, "power": POWER})
                    last_contact = time.ticks_ms()
                    new = clamp_interval(reply.get("interval_s"), interval)
                    if new != interval:
                        # Takes effect at once: the next reading is due `new` s
                        # after the last one — immediately if that's already past.
                        print("[track] interval", interval, "→", new, "s")
                        interval = new
                    if reply.get("measure"):
                        print("[track] dashboard → measuring now")
                        read_due = True
                    ok = True
                except Exception as e:
                    print("[track] Pi unreachable:", e)
            else:
                wifi_fails += 1
                if wifi_fails >= 3:            # a plain reconnect isn't helping
                    wifi_kick()
                    wifi_fails = 0
            next_ping = time.ticks_add(time.ticks_ms(), PING_S * 1000)
            if not ok:                         # don't hammer a Pi that isn't there
                hold_until = next_ping

        if time.ticks_diff(time.ticks_ms(), last_contact) > OFFLINE_RESET_S * 1000:
            print("[track] no contact for", OFFLINE_RESET_S, "s — rebooting")
            time.sleep_ms(200)
            machine.reset()

        time.sleep_ms(100)


# ─────────────────────────────────────── start
def button_held():
    try:
        return WAKE_PIN is not None and Pin(WAKE_PIN, Pin.IN, Pin.PULL_UP).value() == 0
    except Exception:
        return False


# Pause on a manual reset / power-on, and on a button wake — both mean a human is
# present, so give them 5 s to grab the REPL with Ctrl-C (the watchdog is only
# armed afterwards, so it can't bite during maintenance).  Timed wakes skip this.
#
# Once the always-on loop runs, the watchdog is armed and can't be stopped: after
# a Ctrl-C it reboots the board within WDT_S.  For longer work at the REPL, hold
# the button while powering up / pressing reset — the node then stays idle.
_maint = False
if ALWAYS_ON and machine.reset_cause() != machine.DEEPSLEEP_RESET and button_held():
    print("[track] button held at boot — maintenance mode, not starting (reset to run)")
    _maint = True
elif woken_by_button():
    print("[track] woken by button — 5 s maintenance window, Ctrl-C to stop ...")
    time.sleep(5)
elif machine.reset_cause() != machine.DEEPSLEEP_RESET:
    print("[track] boot — 5 s maintenance window, Ctrl-C to stop ...")
    time.sleep(5)

if _maint:
    pass                                       # fall through to the REPL
elif ALWAYS_ON:
    print("[track] always-on mode, heartbeat every", PING_S, "s")
    run_forever()
else:
    run_once()
