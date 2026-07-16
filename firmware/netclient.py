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
try:
    import ujson
except ImportError:                 # CPython (tests)
    import json as ujson


# Protocol field (°C / raw) → (NVS key, is_temp_x10)
_REMOTE_FIELDS = {
    "heat_enabled": ("heat_enabled",   False),
    "temp_enabled": ("temp_enabled",   False),
    "rh_enabled":   ("rh_enabled",     False),
    "idle_enabled": ("idle_enabled",   False),
    "target":       ("target_x10",    True),
    "dt_lower":     ("dt_lower_x10",   True),
    "dt_floor":     ("dt_floor_x10",   True),
    "dt_ceil":      ("dt_ceil_x10",    True),
    "temp_lower":   ("temp_lower_x10", True),
    "temp_upper":   ("temp_upper_x10", True),
    "rh_lower":     ("rh_lower",       False),
    "rh_upper":     ("rh_upper",       False),
    "temp_pwm_f1":  ("temp_pwm_f1",    False),
    "temp_pwm_f2":  ("temp_pwm_f2",    False),
    "rh_pwm_f1":    ("rh_pwm_f1",      False),
    "rh_pwm_f2":    ("rh_pwm_f2",      False),
    "idle_max_idle":("idle_max_idle",  False),
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
                 poll_interval_off=30, curve=None):
        self._cfg   = cfg
        self._host  = host
        self._port  = port
        self._curve = curve      # LocalCurve for autonomous fallback (may be None)
        self.poll_interval_s   = poll_interval_s    # cadence when the Pi answers
        self.poll_interval_off = poll_interval_off  # slow retry when unreachable
        self._fail_limit = fail_limit
        self._last_poll = time.ticks_ms() - poll_interval_s * 1000
        self._fails = 0
        self.online     = False  # Pi reachable
        self.applying   = False  # currently driving setpoints (Pi or local curve)
        self.autonomous = False  # driving from the local curve (Pi unreachable)
        self.pi_time    = 0      # last epoch received from Pi

    @property
    def held_ver(self):
        """Profile version currently stored in flash (0 = none) — advertised to
        the Pi so it only re-sends the curve on a mismatch."""
        return self._curve.ver if self._curve is not None else 0

    # ------------------------------------------------------------------ API
    def sync_mode(self):
        """Call every loop: when the user turns external control off, restore the
        local NVS values and forget any stored profile (works while offline too)."""
        if not self._cfg.ext_ctrl:
            if self.applying:
                self._cfg.reload()
                self.applying = False
                self.autonomous = False
            if self._curve is not None and self._curve.loaded:
                self._curve.clear()

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

    def run_local(self, now_epoch):
        """Autonomous fallback: when the Pi is unreachable, drive setpoints from
        the local flash curve using the NTP wall clock.  No-op while the Pi is
        online (it is authoritative), when external control is off, or without a
        loaded curve / valid time."""
        if self.online or not self._cfg.ext_ctrl:
            return
        if self._curve is None or not self._curve.loaded or now_epoch is None:
            return
        sp = self._curve.current_setpoints(now_epoch)
        if sp:
            self._apply_setpoints(sp)
            self.applying   = True
            self.autonomous = True

    # --------------------------------------------------------------- helpers
    def _apply_setpoints(self, sp):
        for field, (key, is_x10) in _REMOTE_FIELDS.items():
            if field in sp:
                v = sp[field]
                self._cfg.set_volatile(key, int(round(v * 10)) if is_x10 else int(v))

    def _apply(self, data):
        if "time" in data:
            self.pi_time = data["time"]

        # Keep the local flash curve in sync with the Pi's active profile.
        if self._curve is not None:
            prof = data.get("profile")
            if prof and prof.get("points") is not None:
                self._curve.set(prof["points"], prof.get("start_epoch"),
                                prof.get("ver", 0))
            elif not data.get("running") or data.get("prof_ver", 0) == 0:
                if self._curve.loaded:
                    self._curve.clear()   # profile stopped → forget it

        # Drive setpoints from the Pi while external control is on.
        if self._cfg.ext_ctrl and data.get("setpoints"):
            self._apply_setpoints(data["setpoints"])
            self.applying   = True
            self.autonomous = False       # the Pi is driving now
        elif not self._cfg.ext_ctrl and self.applying:
            self._cfg.reload()
            self.applying = False
            self.autonomous = False
