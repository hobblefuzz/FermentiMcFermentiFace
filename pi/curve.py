"""Step profile ("curve") engine.

A profile is a list of points, each with a time offset (seconds from start)
and a partial set of setpoints.  At runtime the active setpoints are the
*forward merge* of every point whose offset has already elapsed — so a point
only needs to list what it changes; everything else carries over.

Named curve.py (not profile.py) to avoid shadowing the stdlib `profile`.
"""

import time
import json


class Profile:
    def __init__(self):
        self.points = []          # [{"t": seconds, "setpoints": {...}}]
        self.started_at = None
        self.running = False

    # ---------------------------------------------------------------- run
    def start(self):
        self.started_at = time.time()
        self.running = True

    def stop(self):
        self.running = False
        self.started_at = None

    def elapsed(self):
        if not self.running or self.started_at is None:
            return 0.0
        return time.time() - self.started_at

    # ------------------------------------------------------------- points
    def set_points(self, points):
        self.points = sorted(points, key=lambda p: p.get("t", 0))

    def current_setpoints(self):
        """Forward-merged setpoints for the elapsed time."""
        merged = {}
        e = self.elapsed()
        for p in self.points:
            if p.get("t", 0) <= e:
                merged.update(p.get("setpoints", {}))
            else:
                break
        return merged

    # ------------------------------------------------------------ persist
    def to_dict(self):
        return {
            "points":  self.points,
            "running": self.running,
            "elapsed": round(self.elapsed(), 1),
        }

    def load(self, path):
        try:
            with open(path) as f:
                self.points = json.load(f).get("points", [])
        except Exception:
            self.points = []

    def save(self, path):
        with open(path, "w") as f:
            json.dump({"points": self.points}, f, indent=2)
