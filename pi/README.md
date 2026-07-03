# Fermenter — Raspberry Pi Server (Phase 1)

Web/control server for the ESP32 fermenter. Receives telemetry, logs it, and
serves the current setpoints (manual or from a running profile).

See [`../PROTOCOL.md`](../PROTOCOL.md) for the ESP32 ↔ Pi wire format.

## Setup

```bash
cd pi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py            # listens on 0.0.0.0:8080
```

Then open `http://<pi-ip>:8080/` for the live status page.

## ESP32 side

Copy `../secrets_example.py` to `../secrets.py`, set `WIFI_SSID`, `WIFI_PASS`,
`PI_HOST` (this Pi's IP) and `PI_PORT` (8080). Flash the firmware, then in the
1602 menu: **Global → WiFi = ON**, and **Ext ctrl = ON** to let the Pi drive
the setpoints.

## Data

- `data/telemetry.db` — SQLite log of every poll (ts, core, chamber, rh, heater, fan1/2)
- `data/profile.json` — the saved profile points

## API quick reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/poll` | ESP32 telemetry → setpoints + time |
| GET | `/api/status` | latest telemetry + profile + setpoints |
| GET | `/api/log?since=<ts>` | telemetry rows for charting |
| GET/POST | `/api/manual` | manual setpoints (used when no profile runs) |
| GET/POST | `/api/profile` | profile points `{"points":[{"t":sec,"setpoints":{...}}]}` |
| POST | `/api/profile/start` · `/stop` | run control |
| POST | `/api/power/shutdown` · `/reboot` | clean power off / restart |

## Safe shutdown (dashboard button)

The **System** card in the dashboard has *Herunterfahren* / *Neustart* buttons so
you never need SSH to power the Pi down safely. The server runs `sudo poweroff`
/ `sudo reboot`, so the service user needs a password-less sudo rule for exactly
those two commands.

Check the paths first, then add the rule:

```bash
which poweroff reboot          # usually /usr/sbin/poweroff, /usr/sbin/reboot
echo "pi ALL=(ALL) NOPASSWD: /usr/sbin/poweroff, /usr/sbin/reboot" \
  | sudo tee /etc/sudoers.d/fermenter
sudo chmod 440 /etc/sudoers.d/fermenter
sudo visudo -c                 # syntax check ("... parsed OK")
```

Replace `pi` with your username if different, and adjust the paths if `which`
shows something else. After that, restart the service and the buttons work.

## Roadmap (Phase 2)

- Rich web UI: live charts (telemetry history) + drag/add profile points
- Move the schedule onto the ESP32 + DS3231 RTC for full autonomy across reboots
