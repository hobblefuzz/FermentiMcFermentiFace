"""Fermenter control server (Raspberry Pi side).

Phase 1: receives ESP32 telemetry, logs it to SQLite, and returns the current
setpoints (from a running profile, or the manual setpoints otherwise).

Run:  python3 server.py   (listens on 0.0.0.0:8080)

Endpoints:
  POST /api/poll             ESP32 telemetry in, setpoints + time out
  GET  /api/status           latest telemetry + profile + active setpoints
  GET  /api/log?since=<ts>   telemetry rows since a unix timestamp
  GET/POST /api/manual       get/set the manual setpoints
  GET/POST /api/profile      get/set the profile points
  POST /api/profile/start|stop
  GET  /                     live status page
"""

import os
import json
import time
import sqlite3
import threading
import subprocess
from flask import Flask, request, jsonify, render_template

from curve import Profile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
DB           = os.path.join(DATA, "telemetry.db")
PROFILE_FILE = os.path.join(DATA, "profile.json")
MANUAL_FILE  = os.path.join(DATA, "manual.json")

app = Flask(__name__)

profile = Profile()
profile.load(PROFILE_FILE)

# Setpoints used when no profile is running (also the base a profile builds on).
manual = {
    "heat_enabled": 1, "temp_enabled": 1, "rh_enabled": 1, "idle_enabled": 1,
    "heat_lower": 28.0, "heat_upper": 30.0, "min_room": 28.0,
    "temp_lower": 36.0, "temp_upper": 38.0,
    "rh_lower": 65, "rh_upper": 85,
    "temp_pwm_f1": 50, "temp_pwm_f2": 50,
    "rh_pwm_f1": 50, "rh_pwm_f2": 50,
    "idle_pwm_f1": 50, "idle_pwm_f2": 50,
}
try:
    with open(MANUAL_FILE) as f:
        manual.update(json.load(f))
except Exception:
    pass

last_telemetry = {}


def _save_manual():
    with open(MANUAL_FILE, "w") as f:
        json.dump(manual, f, indent=2)


def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS telemetry (
        ts REAL, core REAL, chamber REAL, rh REAL,
        heater INTEGER, fan1 INTEGER, fan2 INTEGER, fan TEXT)""")
    return c


# Migrate older DBs that predate the `fan` (fan status) column.
try:
    _c = _db(); _c.execute("ALTER TABLE telemetry ADD COLUMN fan TEXT"); _c.commit(); _c.close()
except Exception:
    pass


def current_setpoints():
    sp = dict(manual)
    if profile.running:
        sp.update(profile.current_setpoints())
    return sp


# ------------------------------------------------------------------- routes
@app.route("/api/poll", methods=["POST"])
def poll():
    global last_telemetry
    tel = request.get_json(force=True, silent=True) or {}
    last_telemetry = tel
    try:
        c = _db()
        c.execute("INSERT INTO telemetry (ts,core,chamber,rh,heater,fan1,fan2,fan) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (time.time(), tel.get("core"), tel.get("chamber"), tel.get("rh"),
                   1 if tel.get("heater") else 0, tel.get("fan1"), tel.get("fan2"),
                   tel.get("fan")))
        c.commit()
        c.close()
    except Exception as e:
        print("[db] error:", e)
    return jsonify({
        "time": int(time.time()),
        "running": profile.running,
        "setpoints": current_setpoints(),
    })


@app.route("/api/status")
def status():
    return jsonify({
        "telemetry": last_telemetry,
        "profile":   profile.to_dict(),
        "setpoints": current_setpoints(),
    })


@app.route("/api/log")
def log():
    since = float(request.args.get("since", time.time() - 3600))
    c = _db()
    rows = c.execute(
        "SELECT ts,core,chamber,rh,heater,fan1,fan2,fan FROM telemetry "
        "WHERE ts>=? ORDER BY ts", (since,)).fetchall()
    c.close()
    return jsonify([
        {"ts": r[0], "core": r[1], "chamber": r[2], "rh": r[3],
         "heater": r[4], "fan1": r[5], "fan2": r[6], "fan": r[7]} for r in rows])


@app.route("/api/manual", methods=["GET", "POST"])
def api_manual():
    if request.method == "POST":
        manual.update(request.get_json(force=True) or {})
        _save_manual()
    return jsonify(manual)


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "POST":
        profile.set_points(request.get_json(force=True).get("points", []))
        profile.save(PROFILE_FILE)
    return jsonify(profile.to_dict())


@app.route("/api/profile/<action>", methods=["POST"])
def profile_action(action):
    if action == "start":
        profile.start()
    elif action == "stop":
        profile.stop()
    return jsonify(profile.to_dict())


@app.route("/api/power/<action>", methods=["POST"])
def power(action):
    """Clean shutdown / reboot of the Pi.  Delayed by a second so the HTTP
    response reaches the browser first.  Requires a sudoers rule allowing the
    service user to run poweroff/reboot without a password (see README)."""
    cmd = {"shutdown": ["sudo", "poweroff"], "reboot": ["sudo", "reboot"]}.get(action)
    if not cmd:
        return jsonify({"ok": False, "error": "unknown action"}), 400
    threading.Timer(1.0, lambda: subprocess.Popen(cmd)).start()
    return jsonify({"ok": True, "action": action})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
