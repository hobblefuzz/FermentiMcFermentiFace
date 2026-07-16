"""Battery temperature tracker (separate ESP32).

Wakes from deep sleep, reads a DS18B20, optionally the battery voltage, POSTs
to the Pi's /api/track, then deep-sleeps again.  Sensor-only — no control,
ignores the response.

Fast wake tips (configure in secrets.py): static IP (skips DHCP), BSSID
(skips the channel scan), and use the Pi's IP (no DNS).
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
DEVICE_ID     = "moromi"     # shows up as its own stream in the dashboard
INTERVAL_S    = 30 * 60      # deep-sleep interval (30 min)
ONEWIRE_PIN   = 4            # DS18B20 data (4.7 kΩ pull-up to 3.3 V)
WIFI_TIMEOUT  = 20           # s to wait for a connection

# Wake button: momentary switch from WAKE_PIN to GND, ends deep sleep on press.
# Must be an RTC-capable GPIO (0, 2, 4, 12-15, 25-27, 32-39) — GPIO34-39 are
# input-only with no internal pull-ups, so they'd need an external one.
# Set to None to disable.
WAKE_PIN      = 33

# Battery sense (optional). Set BATT_ADC_PIN to an ADC1 pin (32-39) and wire a
# divider from the LiPo; leave None to disable. BATT_DIVIDER = Vbatt/Vpin.
BATT_ADC_PIN  = 32
BATT_DIVIDER  = 2.0


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
    if getattr(secrets, "STATIC_IP", None):
        try:
            wlan.ifconfig(secrets.STATIC_IP)   # static → no DHCP round-trip
        except Exception:
            pass
    if not wlan.isconnected():
        bssid = getattr(secrets, "BSSID", None)
        t0 = time.ticks_ms()
        try:
            if bssid:
                wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS, bssid=bssid)
            else:
                wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
        except TypeError:                      # build without bssid kwarg
            wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > WIFI_TIMEOUT * 1000:
                print("[track] WiFi timeout after", WIFI_TIMEOUT, "s")
                return False
            time.sleep_ms(100)
        try:                                   # diagnostics: connect time + signal
            print("[track] WiFi up in {} ms, RSSI {} dBm".format(
                time.ticks_diff(time.ticks_ms(), t0), wlan.status("rssi")))
        except Exception:
            pass
    return True


def post(payload, timeout=4):
    body = ujson.dumps(payload)
    addr = socket.getaddrinfo(secrets.PI_HOST, secrets.PI_PORT)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(addr)
        req = ("POST /api/track HTTP/1.0\r\nHost: {}\r\n"
               "Content-Type: application/json\r\nContent-Length: {}\r\n"
               "Connection: close\r\n\r\n{}").format(secrets.PI_HOST, len(body), body)
        s.send(req.encode())
        s.recv(64)                            # drain; response is ignored
    finally:
        s.close()


# ─────────────────────────────────────── run once, then deep sleep
def run():
    payload = {"id": DEVICE_ID, "temp": read_temp(), "batt": read_batt()}
    if connect():
        try:
            post(payload)
            print("[track] sent:", payload)
        except Exception as e:
            print("[track] post failed:", e)
    else:
        print("[track] no WiFi")


# ─────────────────────────────────────── wake handling
# The wake button is an EXT0 source, so a press still shows up as a
# DEEPSLEEP_RESET — only wake_reason() tells the two apart.  The constant is
# named EXT0_WAKE on most builds and PIN_WAKE on some, hence the lookup.
_EXT0 = getattr(machine, "EXT0_WAKE", getattr(machine, "PIN_WAKE", None))


def woken_by_button():
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


# Pause before sleeping again on a manual reset / power-on, and on a button
# press — both mean a human is present, so give them 5 s to grab the REPL with
# Ctrl-C.  Timed wakes skip this and stay efficient.
_button = woken_by_button()
if _button:
    print("[track] woken by button — 5 s maintenance window, Ctrl-C to stop ...")
    time.sleep(5)
elif machine.reset_cause() != machine.DEEPSLEEP_RESET:
    print("[track] manual boot — 5 s maintenance window, Ctrl-C to stop ...")
    time.sleep(5)

run()
arm_wake_button()
print("[track] deep sleep for", INTERVAL_S, "s (press the wake button to cut it short)")
machine.deepsleep(INTERVAL_S * 1000)
