# Battery Temperature Tracker 🧪 (beta)

> **Beta / experimental.** The firmware works and the node reports reliably while
> powered, but the **battery side is not solved yet** — see [Powering it from a
> battery](#powering-it-from-a-battery). Treat this as a starting point, not a
> finished build. It is entirely optional and independent of the fermenter
> controller.

A second, standalone ESP32 that just tracks one temperature (e.g. a mash in the
fridge) and reports it to the Pi. Sensor-only — it does not control anything.

## Flow

Wake → connect WiFi → read DS18B20 → (optional battery voltage) →
`POST /api/track {id, temp, batt}` → deep sleep `INTERVAL_S`.

Shows up in the dashboard under its own tab (`DEVICE_ID`, default `moromi`).

## Hardware

**Recommended board: WEMOS LOLIN32 Lite.** It has a JST battery connector and a
TP4054 charger onboard, and — unlike a plain dev board — a regulator that still
works at LiPo voltage (see [Powering it from a battery](#powering-it-from-a-battery)).
With it you need **no boost converter and no separate charge module**. It has no
built-in battery divider, so keep the external one; GPIO4/32/33 are all broken out,
so the default pin map fits as-is.

- ESP32 + DS18B20 on `ONEWIRE_PIN` (GPIO 4) with a 4.7 kΩ pull-up to 3.3 V.
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
3. For best battery life set `STATIC_IP` and `BSSID` in `secrets.py`.

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

Press the **wake button** (GPIO 33 → GND): the node wakes immediately, takes a
reading, and — because a press means a human is present — gives you the same 5 s
window to grab the REPL with Ctrl-C as a manual reset does. That is usually all you
need; check the tracker tab or `GET /api/track/latest` on the Pi.

Without a wake button, temporarily comment out the last `machine.deepsleep(...)`
line so it runs, prints, and stops.
