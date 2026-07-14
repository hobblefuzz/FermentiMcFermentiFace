"""On-device profile curve — a local copy of the Pi's step profile so the ESP32
can run the schedule autonomously when the Pi is unreachable.

Stored as JSON in flash (CURVE_FILE).  A profile is {start_epoch, ver, points};
each point is {"t": seconds-from-start, "setpoints": {...}}.  current_setpoints()
forward-merges every point whose offset has already elapsed — exactly like the
Pi's curve.py — so both sides derive the same step from the same start_epoch.

`start_epoch` is a Unix epoch (matches the Pi); pass the NTP-synced wall clock
(timesync.TimeSync.unix_time()) to the query methods.  This module only stores
and computes; deciding when to apply the result (Pi-present vs autonomous) is the
caller's job (main loop, wired in 3d).
"""

try:
    import ujson
except ImportError:                 # CPython (tests)
    import json as ujson

CURVE_FILE = "curve.json"


class LocalCurve:
    def __init__(self, path=CURVE_FILE):
        self._path       = path
        self.points      = []
        self.start_epoch = None     # Unix epoch, from the Pi
        self.ver         = 0        # profile version; 0 = nothing loaded
        self.load()

    # --------------------------------------------------------------- persist
    def load(self):
        try:
            with open(self._path) as f:
                d = ujson.load(f)
            self.points      = d.get("points", [])
            self.start_epoch = d.get("start_epoch")
            self.ver         = d.get("ver", 0)
        except Exception:
            self.points = []
            self.start_epoch = None
            self.ver = 0

    def save(self):
        try:
            with open(self._path, "w") as f:
                ujson.dump({"points": self.points,
                            "start_epoch": self.start_epoch,
                            "ver": self.ver}, f)
        except Exception as e:
            print("[curve] save failed:", e)

    def set(self, points, start_epoch, ver):
        """Adopt a curve pushed from the Pi and persist it to flash."""
        self.points      = sorted(points, key=lambda p: p.get("t", 0))
        self.start_epoch = start_epoch
        self.ver         = ver
        self.save()

    def clear(self):
        """Forget the stored profile (dashboard stop / local ext_ctrl off)."""
        self.points = []
        self.start_epoch = None
        self.ver = 0
        try:
            import os
            os.remove(self._path)
        except Exception:
            pass

    # ---------------------------------------------------------------- query
    @property
    def loaded(self):
        return self.ver != 0 and self.start_epoch is not None and len(self.points) > 0

    def elapsed(self, now_epoch):
        """Seconds since start for the given wall clock, or None if unusable."""
        if not self.loaded or now_epoch is None:
            return None
        return now_epoch - self.start_epoch

    def current_setpoints(self, now_epoch):
        """Forward-merged setpoints for the given wall clock, or None."""
        e = self.elapsed(now_epoch)
        if e is None:
            return None
        merged = {}
        for p in self.points:
            if p.get("t", 0) <= e:
                merged.update(p.get("setpoints", {}))
            else:
                break
        return merged

    def current_index(self, now_epoch):
        """Index of the active step (-1 before the first / when unusable)."""
        e = self.elapsed(now_epoch)
        if e is None:
            return -1
        idx = -1
        for i, p in enumerate(self.points):
            if p.get("t", 0) <= e:
                idx = i
            else:
                break
        return idx
