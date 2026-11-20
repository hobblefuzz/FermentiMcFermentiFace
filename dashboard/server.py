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
import io
import csv
import json
import time
import datetime
import sqlite3
import threading
import subprocess
from flask import Flask, request, jsonify, render_template, Response

from curve import Profile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
DB            = os.path.join(DATA, "telemetry.db")
PROFILE_FILE  = os.path.join(DATA, "profile.json")
MANUAL_FILE   = os.path.join(DATA, "manual.json")
PROFILES_FILE = os.path.join(DATA, "profiles.json")   # named profile library
TRACKER_CFG_FILE = os.path.join(DATA, "tracker_config.json")  # per-tracker settings

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True   # pick up index.html edits without a restart
app.jinja_env.auto_reload = True

profile = Profile()
profile.load(PROFILE_FILE)

# Factory defaults — kept in sync with the ESP32's config.py.
FACTORY = {
    "heat_enabled": 1, "temp_enabled": 1, "rh_enabled": 1, "idle_enabled": 1,
    "target": 30.0, "dt_lower": 0.5, "dt_floor": 2.0, "dt_ceil": 2.0,
    "temp_lower": 31.0, "temp_upper": 32.0,
    "temp_pwm_f1": 100, "temp_pwm_f2": 100,
    "rh_lower": 85, "rh_upper": 90,
    "rh_pwm_f1": 50, "rh_pwm_f2": 35,
    "idle_max_idle": 30,
    "idle_pwm_f1": 35, "idle_pwm_f2": 50,
}
# Setpoints used when no profile is running (also the base a profile builds on).
manual = dict(FACTORY)
try:
    with open(MANUAL_FILE) as f:
        saved = json.load(f)
    # Only adopt keys we still know about, so orphaned pre-redesign keys
    # (heat_lower/heat_upper/min_room) don't linger and get pushed.
    manual.update({k: v for k, v in saved.items() if k in FACTORY})
except Exception:
    pass

# Named profile library: {name: [points]}
profiles = {}
try:
    with open(PROFILES_FILE) as f:
        profiles = json.load(f)
except Exception:
    profiles = {}

last_telemetry = {}
last_tracker = {}   # device_id -> {"ts", "temp", "raw", "batt", "interval_s", "power"}
tracker_seen = {}   # device_id -> time of the last contact (reading or heartbeat)
tracker_measure = set()   # device ids with a pending "measure now" request

# Per-tracker settings, edited in the dashboard and handed to the node in the
# reply to every heartbeat / reading.  Offset and alarm limits are applied here
# on the Pi; only the interval actually travels to the node.
TRACKER_DEFAULTS = {"interval_s": 1800, "offset": 0.0, "alarm_min": None, "alarm_max": None}
TRACKER_INTERVAL_MIN, TRACKER_INTERVAL_MAX = 10, 24 * 3600
tracker_cfg = {}
try:
    with open(TRACKER_CFG_FILE) as f:
        tracker_cfg = json.load(f)
except Exception:
    tracker_cfg = {}


def _save_manual():
    with open(MANUAL_FILE, "w") as f:
        json.dump(manual, f, indent=2)


def _save_tracker_cfg():
    with open(TRACKER_CFG_FILE, "w") as f:
        json.dump(tracker_cfg, f, indent=2)


def _tcfg(dev):
    """Effective settings for a tracker: defaults overlaid with what was saved."""
    c = dict(TRACKER_DEFAULTS)
    c.update(tracker_cfg.get(dev, {}))
    return c


def _num(v):
    """float(v), or None for empty / missing / unparsable input."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _node_reply(dev):
    """What a tracker node gets back: its interval + a pending measure request."""
    reply = {"ok": True, "interval_s": int(_tcfg(dev)["interval_s"])}
    if dev in tracker_measure:
        tracker_measure.discard(dev)
        reply["measure"] = True
    return reply


def _save_profiles():
    with open(PROFILES_FILE, "w") as f:
        json.dump(profiles, f, indent=2)


def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS telemetry (
        ts REAL, core REAL, chamber REAL, rh REAL,
        heater INTEGER, fan1 INTEGER, fan2 INTEGER, fan TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, name TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tracker (ts REAL, device TEXT, temp REAL, batt REAL)")
    return c


_last_step = -2   # index of the last-logged profile step (-2 = not running)
logging_on = True # whether telemetry is written to the DB (auto-on at profile start)


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
    global last_telemetry, _last_step
    tel = request.get_json(force=True, silent=True) or {}
    last_telemetry = tel

    if logging_on:
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

        # Log profile step transitions as events (chart markers + history)
        if profile.running:
            idx = profile.current_index()
            if idx >= 0 and idx != _last_step:
                _last_step = idx
                try:
                    cc = _db()
                    cc.execute("INSERT INTO events VALUES (?,?)", (time.time(), profile.current_name()))
                    cc.commit(); cc.close()
                except Exception as e:
                    print("[db] event error:", e)
        else:
            _last_step = -2

    resp = {
        "time": int(time.time()),
        "running": profile.running,
        "setpoints": current_setpoints(),
        # prof_ver identifies the active curve; 0 = no running profile → the
        # ESP32 clears its local copy.  The full curve rides along only when the
        # ESP32 advertises a different version (start / edit / it lost its copy).
        "prof_ver": profile.ver if profile.running else 0,
    }
    if profile.running and tel.get("prof_ver", 0) != profile.ver:
        resp["profile"] = {
            "start_epoch": profile.started_at,   # Unix epoch, matches the ESP32
            "ver":         profile.ver,
            "points":      profile.points,
        }
    return jsonify(resp)


@app.route("/api/status")
def status():
    return jsonify({
        "telemetry": last_telemetry,
        "profile":   profile.to_dict(),
        "setpoints": current_setpoints(),
        "logging":   logging_on,
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


@app.route("/api/manual/reset", methods=["POST"])
def api_manual_reset():
    manual.clear()
    manual.update(FACTORY)
    _save_manual()
    return jsonify(manual)


# ------------------------------------------------------ named profile library
@app.route("/api/profiles")
def profiles_list():
    return jsonify(sorted(profiles.keys()))


@app.route("/api/profiles/<name>", methods=["GET", "POST", "DELETE"])
def profiles_item(name):
    if request.method == "POST":
        profiles[name] = request.get_json(force=True).get("points", [])
        _save_profiles()
        return jsonify({"ok": True})
    if request.method == "DELETE":
        profiles.pop(name, None)
        _save_profiles()
        return jsonify({"ok": True})
    return jsonify({"points": profiles.get(name, [])})


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "POST":
        profile.set_points(request.get_json(force=True).get("points", []))
        profile.save(PROFILE_FILE)
    return jsonify(profile.to_dict())


@app.route("/api/profile/<action>", methods=["POST"])
def profile_action(action):
    global _last_step, logging_on
    if action == "start":
        profile.start()
        _last_step = -2          # ensure the first step is logged on next poll
        logging_on = True        # auto-start logging with the profile
    elif action == "stop":
        profile.stop()
    profile.save(PROFILE_FILE)   # persist run-state so a restart resumes it
    return jsonify(profile.to_dict())


@app.route("/api/events")
def events():
    since = float(request.args.get("since", time.time() - 3600))
    c = _db()
    rows = c.execute("SELECT ts,name FROM events WHERE ts>=? ORDER BY ts", (since,)).fetchall()
    c.close()
    return jsonify([{"ts": r[0], "name": r[1]} for r in rows])


# ------------------------------------------------------------- logging control
@app.route("/api/logging/<action>", methods=["POST"])
def logging_ctl(action):
    global logging_on
    if action == "on":
        logging_on = True
    elif action == "off":
        logging_on = False
    elif action == "toggle":
        logging_on = not logging_on
    return jsonify({"logging": logging_on})


@app.route("/api/log/reset", methods=["POST"])
def log_reset():
    c = _db()
    c.execute("DELETE FROM telemetry")
    c.execute("DELETE FROM events")
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/export.csv")
def export_csv():
    c = _db()
    rows = c.execute("SELECT ts,core,chamber,rh,heater,fan1,fan2,fan "
                     "FROM telemetry ORDER BY ts").fetchall()
    c.close()

    # German-Excel friendly: ';' as column separator, ',' as decimal point.
    def de(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return str(v).replace(".", ",")
        return str(v)

    buf = io.StringIO()
    buf.write("sep=;\r\n")                    # tells Excel the delimiter
    w = csv.writer(buf, delimiter=";")
    w.writerow(["timestamp", "datetime", "core_c", "chamber_c", "rh_pct",
                "heater", "fan1_pct", "fan2_pct", "fan_state"])
    for r in rows:
        dt = datetime.datetime.fromtimestamp(r[0]).isoformat(sep=" ", timespec="seconds")
        w.writerow([de(r[0]), dt, de(r[1]), de(r[2]), de(r[3]),
                    de(r[4]), de(r[5]), de(r[6]), de(r[7])])
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=fermenter_log.csv"})


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


# ------------------------------------------------------ sensor-only trackers
@app.route("/api/track", methods=["POST"])
def track():
    t = request.get_json(force=True, silent=True) or {}
    dev = str(t.get("id") or "tracker")
    now = time.time()
    raw = _num(t.get("temp"))
    batt = _num(t.get("batt"))
    # Calibration offset is applied here, so the log, chart and CSV all carry
    # the corrected value and the node never needs to know about it.
    temp = None if raw is None else round(raw + float(_tcfg(dev)["offset"] or 0), 2)
    reply = _node_reply(dev)
    # interval_s = what the node runs on from now on (the one we hand it back),
    # so the dashboard can estimate the next reading even for a sleeping node.
    last_tracker[dev] = {"ts": now, "temp": temp, "raw": raw, "batt": batt,
                         "interval_s": reply["interval_s"], "power": t.get("power")}
    tracker_seen[dev] = now
    try:
        c = _db()
        c.execute("INSERT INTO tracker VALUES (?,?,?,?)", (now, dev, temp, batt))
        c.commit(); c.close()
    except Exception as e:
        print("[db] track error:", e)
    return jsonify(reply)


@app.route("/api/track/ping", methods=["POST"])
def track_ping():
    """Heartbeat from an always-on (mains-powered) tracker, every few seconds.

    Keeps the dashboard's "last signal" fresh between readings and hands the
    node its current interval plus any pending "measure now" request — so a
    change in the dashboard takes effect within one heartbeat.
    """
    t = request.get_json(force=True, silent=True) or {}
    dev = str(t.get("id") or "tracker")
    tracker_seen[dev] = time.time()
    if dev in last_tracker:
        last_tracker[dev]["power"] = t.get("power", last_tracker[dev].get("power"))
    return jsonify(_node_reply(dev))


@app.route("/api/track/config", methods=["GET", "POST"])
def track_config():
    """Dashboard: read / change one tracker's settings (device=<id>)."""
    dev = request.args.get("device", "moromi")
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        cur = _tcfg(dev)
        if "interval_s" in d and _num(d["interval_s"]) is not None:
            cur["interval_s"] = int(max(TRACKER_INTERVAL_MIN,
                                        min(TRACKER_INTERVAL_MAX, _num(d["interval_s"]))))
        if "offset" in d:
            off = _num(d["offset"]) or 0.0
            cur["offset"] = max(-10.0, min(10.0, off))
        for k in ("alarm_min", "alarm_max"):
            if k in d:
                cur[k] = _num(d[k])            # empty field → no limit
        tracker_cfg[dev] = cur
        _save_tracker_cfg()
    return jsonify(_tcfg(dev))


@app.route("/api/track/measure", methods=["POST"])
def track_measure():
    """Ask an always-on tracker for a reading now (delivered with its next heartbeat)."""
    dev = request.args.get("device", "moromi")
    tracker_measure.add(dev)
    return jsonify({"ok": True})


@app.route("/api/track/reset", methods=["POST"])
def track_reset():
    """Delete a tracker's logged rows (device=<id>), or all if unspecified."""
    dev = request.args.get("device")
    c = _db()
    if dev:
        c.execute("DELETE FROM tracker WHERE device=?", (dev,))
        last_tracker.pop(dev, None)
        tracker_seen.pop(dev, None)
    else:
        c.execute("DELETE FROM tracker")
        last_tracker.clear()
        tracker_seen.clear()
    c.commit(); c.close()
    return jsonify({"ok": True})


@app.route("/api/track/latest")
def track_latest():
    """Latest reading per tracker, plus last contact and current settings."""
    out = {}
    for dev in set(last_tracker) | set(tracker_seen):
        e = dict(last_tracker.get(dev, {}))
        e["seen"] = tracker_seen.get(dev)
        e["config"] = _tcfg(dev)
        out[dev] = e
    return jsonify(out)


@app.route("/api/track/devices")
def track_devices():
    """Every tracker id we know of, so the dashboard can offer a picker.

    Read from the DB (survives a restart) and unioned with the in-memory
    last_tracker, so a node that just reported shows up right away.  Trackers
    register themselves simply by POSTing — nothing to configure here.
    """
    devs = set(last_tracker) | set(tracker_seen)
    try:
        c = _db()
        devs |= {r[0] for r in c.execute(
            "SELECT DISTINCT device FROM tracker WHERE device IS NOT NULL")}
        c.close()
    except Exception as e:
        print("[db] devices error:", e)
    return jsonify(sorted(devs))


@app.route("/api/track/log")
def track_log():
    dev = request.args.get("device", "moromi")
    since = float(request.args.get("since", time.time() - 24 * 3600))
    c = _db()
    rows = c.execute("SELECT ts,temp,batt FROM tracker WHERE device=? AND ts>=? ORDER BY ts",
                     (dev, since)).fetchall()
    c.close()
    return jsonify([{"ts": r[0], "temp": r[1], "batt": r[2]} for r in rows])


@app.route("/api/track/export.csv")
def track_export_csv():
    dev = request.args.get("device", "moromi")
    c = _db()
    rows = c.execute("SELECT ts,temp,batt FROM tracker WHERE device=? ORDER BY ts",
                     (dev,)).fetchall()
    c.close()

    def de(v):
        if v is None:
            return ""
        return str(v).replace(".", ",") if isinstance(v, float) else str(v)

    buf = io.StringIO()
    buf.write("sep=;\r\n")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["timestamp", "datetime", "temp_c", "batt_v"])
    for r in rows:
        dt = datetime.datetime.fromtimestamp(r[0]).isoformat(sep=" ", timespec="seconds")
        w.writerow([de(r[0]), dt, de(r[1]), de(r[2])])
    # Quote the filename and strip anything awkward — a device id may contain
    # spaces, which an unquoted Content-Disposition token can't carry (browsers
    # truncate the name there).
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in dev) or "tracker"
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s_log.csv"' % safe})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
