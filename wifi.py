"""Minimal WiFi station manager.

Non-blocking after the initial connect: reconnect attempts just re-issue
connect() (which is async) and poll isconnected(), so the main loop never
stalls waiting for WiFi.
"""

import network
import time


class WiFi:
    def __init__(self, ssid, password):
        self._ssid = ssid or ""
        self._pw   = password or ""
        self._wlan = network.WLAN(network.STA_IF)

    def connect(self, timeout_s=15):
        """Blocking connect with timeout — call once at boot (before WDT arm)."""
        if not self._ssid:
            return False
        self._wlan.active(True)
        if self._wlan.isconnected():
            return True
        self._wlan.connect(self._ssid, self._pw)
        t0 = time.ticks_ms()
        while not self._wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_s * 1000:
                return False
            time.sleep_ms(200)
        return True

    def ensure(self):
        """Non-blocking reconnect nudge — call periodically from the loop.
        Returns True if connected."""
        if not self._ssid:
            return False
        if self._wlan.isconnected():
            return True
        # kick off an async (re)connect; isconnected() will flip when ready
        try:
            self._wlan.active(True)
            self._wlan.connect(self._ssid, self._pw)
        except Exception:
            pass
        return False

    @property
    def connected(self):
        return self._wlan.isconnected()

    def ip(self):
        try:
            return self._wlan.ifconfig()[0] if self._wlan.isconnected() else None
        except Exception:
            return None

    def off(self):
        try:
            self._wlan.disconnect()
            self._wlan.active(False)
        except Exception:
            pass
