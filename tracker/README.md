# Temperature Tracker 🧪 (beta)

> **Beta / experimental.** On **USB power** (always-on mode) it is simple and
> reliable. The **battery side is not solved yet** — see [Powering it from a
> battery](#powering-it-from-a-battery). It is entirely optional and independent
> of the fermenter controller.

A second, standalone ESP32 that just tracks one temperature (e.g. a moromi in the
fridge) and reports it to the Pi. Sensor-only — it does not control anything.

Shows up in the dashboard under its own tab (`DEVICE_ID`, default `moromi`).

## Two modes

`ALWAYS_ON` in `main.py` picks one:

| | **Always-on** (`True`, default) | **Deep sleep** (`False`) |
|---|---|---|
| Power | USB / mains | LiPo |
| Talks to the Pi | heartbeat every `PING_S` (10 s) + a reading every interval | once per wake |
| New interval from the dashboard | within ~10 s | at the next wake |
| "Measure now" | ✅ dashboard button or the hardware button | hardware button only |
| Dashboard shows | **Signal** — age of the last heartbeat (red after 60 s) | battery voltage |
| Self-healing | watchdog, WiFi radio reset, reboot after 10 min without the Pi | fresh boot every wake anyway |

**Always-on flow:** boot → 5 s maintenance window → loop: heartbeat
`POST /api/track/ping` every 10 s (reply carries the interval and any "measure
now"), reading `POST /api/track` whenever the interval has passed.

**Deep-sleep flow:** wake → read DS18B20 (+ battery) → `POST /api/track` →
deep sleep for the interval in the reply (`INTERVAL_S` if the Pi was unreachable).

## Dashboard settings

The tracker tab has a **Tracker settings** card, stored per device on the Pi
(`dashboard/data/tracker_config.json`):

- **Interval** — 10 s … 24 h.
- **Calibration offset** — added on the Pi to every *new* reading (log, chart,
  CSV all carry the corrected value; the raw one is kept in `/api/track/latest`).
  Readings already stored are not changed.
- **Temperature alarm** — min/max; the temperature tile turns red outside the band.
  Empty = no limit.

The status card also flags **Approx. next reading** red once it is overdue —
the quickest hint that a sleeping node did not wake up.

## Protocol

All bodies JSON, all replies `{"ok": true, "interval_s": <s>}` plus `"measure": true`
when a reading was requested from the dashboard (delivered once).

| Endpoint | Sender | Body |
|---|---|---|
| `POST /api/track` | node | `{id, temp, batt, power, interval_s}` — a reading |
| `POST /api/track/ping` | node (always-on) | `{id, power}` — heartbeat |
| `GET/POST /api/track/config?device=` | dashboard | `{interval_s, offset, alarm_min, alarm_max}` |
| `POST /api/track/measure?device=` | dashboard | — queue a "measure now" |
| `GET /api/track/latest` | dashboard | latest reading + `seen` + `config` per device |

## Running several trackers

Nothing to configure on the Pi: a node **registers itself by posting**. Give each
one its own `DEVICE_ID` in `main.py` and they are logged, charted and exported
separately; the tracker tab grows a device picker as soon as a second one reports.

Handy when swapping hardware — run the new node alongside the old one under a
different id, compare the two, and only retire the old one once you trust the new.

> ⚠️ *Reset plot* deletes the readings of the **selected** device only, and the
> confirmation names it — check that name before confirming.

## Hardware — USB (always-on)

Any ESP32 dev board, powered from a USB charger — the onboard regulator is happy
on 5 V, so the board's battery problems below don't apply.

```
DS18B20          ESP32
VDD  (red)    →  3V3
GND  (black)  →  GND
DATA (yellow) →  GPIO 15     + 4.7 kΩ from DATA to 3V3
```

- **The pull-up is not optional.** The ESP32's internal pull-up (~45 kΩ, which the
  MicroPython driver already switches on) is about ten times too weak for 1-Wire —
  it may pass on a bench with 10 cm of wire and then fail with a cable into the
  fridge, typically as sporadic *no reading*. The easiest solder-free fix is a
  **DS18B20 adapter board** (screw terminal + built-in 4.7 kΩ), wired to the ESP32
  with three jumper wires.
- Long cable (several metres) or still flaky: go down to 2.2 kΩ (or 1.5 kΩ), and
  add a 100 nF capacitor between VDD and GND at the sensor end.
- Power the sensor from **3V3, not 5 V** — with a 5 V pull-up the data line would
  sit above the ESP32's 3.6 V GPIO limit.
- Keep the ESP32 **outside the fridge** (metal box = poor WiFi); the flat sensor
  cable fits through the door gasket.
- Leave `BATT_ADC_PIN = None`.
- GPIO 15 is a strapping pin, but harmlessly so: it only has to be high at boot
  (else the boot log is muted), and the pull-up keeps it there. Avoid GPIO 12 for
  the sensor — high at boot, it sets the wrong flash voltage and the board won't start.

### Working on an always-on node

After the 5 s boot window a **hardware watchdog** runs — it can't be stopped, so
a Ctrl-C at the REPL ends in a reboot within `WDT_S` (60 s). For flashing:

- press reset and start `mpremote` within the 5 s window, or
- **hold the button (GPIO 33 → GND) while resetting** — the node then stays idle
  at the REPL until the next reset.

## Hardware — battery (deep sleep)

**Recommended board: WEMOS LOLIN32 Lite.** It has a JST battery connector and a
TP4054 charger onboard, and — unlike a plain dev board — a regulator that still
works at LiPo voltage (see [Powering it from a battery](#powering-it-from-a-battery)).
With it you need **no boost converter and no separate charge module**. It has no
built-in battery divider, so keep the external one; GPIO15/32/33 are all broken out,
so the default pin map fits as-is.

- ESP32 + DS18B20 on `ONEWIRE_PIN` (GPIO 15, labelled D15) with a 4.7 kΩ pull-up to 3.3 V.
- **Wake button** (optional): momentary switch from `WAKE_PIN` (GPIO 33) to GND —
  ends the deep sleep immediately and takes a reading. Handy for debugging and for
  an on-demand measurement. Must be an RTC-capable GPIO; avoid GPIO34–39, which are
  input-only and have no internal pull-ups. Set `WAKE_PIN = None` to disable.
  > If presses are missed or the node wakes spuriously, add an external 10 kΩ
  > pull-up from the pin to 3.3 V — the internal one is not reliable on every build.
- **Power switch** (recommended): the LOLIN32 Lite has none, so put a slide switch
  in the battery line — otherwise the node keeps draining while unused.
- **Keep the ESP32 outside the fridge**, run the DS18B20 on a cable through the
  door gasket — metal fridges block WiFi, and a cold LiPo loses capacity.
- Optional battery sense: a divider (e.g. 2×220 kΩ) from LiPo+ to an **ADC1**
  pin (GPIO 32–39, *not* ADC2 — blocked while WiFi is on). Set `BATT_ADC_PIN`
  and `BATT_DIVIDER` in `main.py`.

## Setup

1. Flash MicroPython. Upload `main.py` and `lib`-free (`onewire`/`ds18x20` are
   built in).
2. Copy `secrets_example.py` → `secrets.py`, fill in WiFi + `PI_HOST`/`PI_PORT`.
3. Set `DEVICE_ID`, and `ALWAYS_ON` (`True` on USB, `False` on battery).
4. Battery only: for best battery life set `STATIC_IP` and `BSSID` in
   `secrets.py`, and `BATT_ADC_PIN` if you wired a divider.

The interval, offset and alarm limits are set in the dashboard, not here —
`INTERVAL_S` is only the fallback until the node has heard from the Pi.

## Powering it from a battery

This is the unsolved part, and the reason this node is marked beta. **A standard
ESP32 dev board is a poor battery device**, for two independent reasons.

### 1. The regulator can't run from a single LiPo

Most dev boards carry an **AMS1117** LDO, which needs roughly **1.0–1.3 V more on
its input than it outputs** (dropout). For a stable 3.3 V it wants ≥ 4.3 V:

- On **USB (5 V)** it regulates properly → 3.3 V. ✅
- On a **1S LiPo (3.7–4.2 V)** it is *in dropout* → it stops regulating and simply
  passes through roughly `Vin − dropout`. Measured here: **3.8 V in → 2.8 V out**,
  which is below spec for both the ESP32 and the DS18B20. ❌

Symptoms are misleading: it works on USB, browns out on battery, and every extra
load on the 3.3 V rail drags it down further (because nothing is regulating).

**Fixes**, best first:

1. **Use a battery-oriented board** — LOLIN32 Lite, LOLIN D32, Adafruit Feather
   ESP32, TinyPICO. They have a regulator that works down to LiPo voltage and
   onboard charging, which solves the regulator *and* the charging problem at once.
   This is what the recommendation above is based on.
2. **Boost converter** — feed a plain dev board's 5 V/Vin pin from a boost
   converter (e.g. MT3608) so the LDO has headroom again. Works, but the
   boost + LDO chain is lossy and the board's quiescent current (below) remains.

> ⚠️ Don't power the DS18B20 directly from the LiPo to dodge this: its 1-Wire
> pull-up would pull the data line to ~4.2 V, above the ESP32's 3.6 V GPIO limit.
> Sensor and MCU must share the same 3.3 V rail.

### 2. The board's quiescent current dominates

The ESP32 *chip* sleeps at ~10 µA. The *board around it* does not:

| Consumer | Deep-sleep draw |
|---|---|
| ESP32 chip | ~10 µA |
| AMS1117 regulator | ~5 mA |
| CP2102 USB-serial | mA range |
| Power LED | ~2–5 mA |

So a plain DevKit burns **milliamps while "asleep"** — that, not the 30-minute
wakes, is what drains the cell. Removing the power LED is the cheapest big win;
a battery-oriented board is the real fix.

### Runtime estimate

Wake cost itself is modest: ≈ 2–4 s at ~250 mA per wake, so at a 30-min interval
the *wakes* are not the problem — the **sleep current is**, and that is a property
of your board, not of this firmware.

> ⚠️ **Measure it; don't trust datasheets here.** Spec sheets and many board pages
> quote the ESP32 *chip*'s ~5–10 µA deep-sleep figure, which says nothing about the
> regulator, USB-serial chip and LED sitting next to it. A plain DevKit measured in
> the milliamps. The LOLIN32 Lite should be far better, but its actual sleep current
> is not reliably documented anywhere we could find — so put a meter in the battery
> line before promising yourself a runtime.

With a genuinely low sleep current, a 2000 mAh cell lasts weeks to months at a
30-min interval (self-discharge often limits first). With a DevKit, expect days.

## Testing

Always-on: hit **⟳ Measure now** in the dashboard or press the button — the
reading shows up within a few seconds. The REPL prints every heartbeat result
that changes something (`interval 1800 → 60 s`, `dashboard → measuring now`).

Deep sleep: press the **wake button** (GPIO 33 → GND): the node wakes immediately, takes a
reading, and — because a press means a human is present — gives you the same 5 s
window to grab the REPL with Ctrl-C as a manual reset does. That is usually all you
need; check the tracker tab or `GET /api/track/latest` on the Pi.

Without a wake button, temporarily set `ALWAYS_ON = True` to watch it read and
report in a loop.
