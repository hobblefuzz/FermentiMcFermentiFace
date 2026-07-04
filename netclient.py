"""HTTP-poll client: pushes telemetry to the Pi, applies returned setpoints.

Uses a raw socket with settimeout() rather than urequests, so a slow or
unreachable Pi can never block the main loop longer than `timeout` seconds
(kept below the watchdog window).

Setpoints are applied to the Config *volatile* cache only (no NVS write) and
only while the user has enabled external control (cfg.ext_ctrl).  Turning
external control off restores the persisted local values via cfg.reload().
"""

import socket
import time
import ujson


# Protocol field (°C / raw) → (NVS key, is_temp_x10)
_REMOTE_FIELDS = {
    "heat_enabled": ("heat_enabled",   False),
    "temp_enabled": ("temp_enabled",   False),
    "rh_enabled":   ("rh_enabled",     False),
    "idle_enabled": ("idle_enabled",   False),
    "heat_lower":   ("heat_lower_x10", True),
    "heat_upper":   ("heat_upper_x10", True),
    "min_room":     ("min_room_x10",   True),
    "temp_lower":   ("temp_lower_x10", True),
    "temp_upper":   ("temp_upper_x10", True),
    "rh_lower":     ("rh_lower",       False),
    "rh_upper":     ("rh_upper",       False),
    "temp_pwm_f1":  ("temp_pwm_f1",    False),
    "temp_pwm_f2":  ("temp_pwm_f2",    False),
    "rh_pwm_f1":    ("rh_pwm_f1",      False),
    "rh_pwm_f2":    ("rh_pwm_f2",      False),
    "idle_pwm_f1":  ("idle_pwm_f1",    False),
    "idle_pwm_f2":  ("idle_pwm_f2",    False),
}


def _post_json(host, port, path, obj, timeout=2):
    """Bare-bones HTTP/1.0 JSON POST with a hard socket timeout."""
    body = ujson.dumps(obj)
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(addr)
        req = ("POST {} HTTP/1.0\r\nHost: {}\r\n"
               "Content-Type: application/json\r\n"
               "Content-Length: {}\r\nConnection: close\r\n\r\n{}"
               ).format(path, host, len(body), body)
        s.send(req.encode())
        resp = b""
        while True:
            chunk = s.recv(512)
            if not chunk:
                break
            resp += chunk
    finally:
        s.close()
    if b"\r\n\r\n" in resp:
        _, _, payload = resp.partition(b"\r\n\r\n")
        if payload:
            return ujson.loads(payload)
    return None


class NetClient:
    def __init__(self, cfg, host, port, poll_interval_s=5, fail_limit=3,
                 poll_interval_off=30):
        self._cfg  = cfg
        self._host = host
        self._port = port
        self.poll_interval_s   = poll_interval_s    # cadence when the Pi answers
        self.poll_interval_off = poll_interval_off  # slow retry when unreachable
        self._fail_limit = fail_limit
        self._last_poll = time.ticks_ms() - poll_interval_s * 1000
        self._fails = 0
        self.online   = False    # Pi reachable
        self.applying = False    # currently driving setpoints from the Pi
        self.pi_time  = 0        # last epoch received from Pi

    # ------------------------------------------------------------------ API
    def sync_mode(self):
        """Call every loop: restore local NVS values when the user turns
        external control off (works even while the Pi is offline)."""
        if not self._cfg.ext_ctrl and self.applying:
            self._cfg.reload()
            self.applying = False

    def due(self):
        # Back off to the slow cadence once the Pi has failed to answer
        # repeatedly, so a dead Pi doesn't stall the loop every few seconds.
        interval = self.poll_interval_off if self._fails >= self._fail_limit else self.poll_interval_s
        return time.ticks_diff(time.ticks_ms(), self._last_poll) >= interval * 1000

    def poll(self, telemetry):
        self._last_poll = time.ticks_ms()
        try:
            data = _post_json(self._host, self._port, "/api/poll", telemetry)
            self._fails = 0
            self.online = True
            if data:
                self._apply(data)
            return True
        except Exception as e:
            self._fails += 1
            if self._fails >= self._fail_limit:
                self.online = False
            print("[net] poll failed:", e)
            return False

    # --------------------------------------------------------------- helpers
    def _apply(self, data):
        if "time" in data:
            self.pi_time = data["time"]
        # Only drive setpoints when the user has enabled external control.
        if self._cfg.ext_ctrl and data.get("setpoints"):
            sp = data["setpoints"]
            for field, (key, is_x10) in _REMOTE_FIELDS.items():
                if field in sp:
                    v = sp[field]
                    self._cfg.set_volatile(key, int(round(v * 10)) if is_x10 else int(v))
            self.applying = True
        elif not self._cfg.ext_ctrl and self.applying:
            self._cfg.reload()
            self.applying = False
