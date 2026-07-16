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

- ESP32 + DS18B20 on `ONEWIRE_PIN` (GPIO 4) with a 4.7 kΩ pull-up to 3.3 V.
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

1. **Use a battery-oriented board** — Adafruit Feather ESP32, LOLIN D32, TinyPICO.
   They have a regulator that works down to LiPo voltage, onboard charging, and
   µA-range deep sleep. Solves regulator, charging and runtime in one go.
2. **Boost converter** — feed the board's 5 V/Vin pin from a boost converter
   (e.g. MT3608) so the LDO has headroom again. Works, but the boost + LDO chain
   is lossy and the board's quiescent current (below) remains.

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
a low-power board is the real fix.

### Runtime estimate

Wake cost itself is modest: ≈ 2–4 s at ~250 mA per wake. On a **low-power board**
at a 30-min interval, a 2000 mAh cell lasts weeks to months (self-discharge often
limits first). On a **standard DevKit**, expect days at best. Measure your board's
sleep current before trusting any estimate.

## Testing

To test without the 30-min wait, temporarily comment out the last
`machine.deepsleep(...)` line so it runs, prints, and stops — then check the
Moromi tab / `GET /api/track/latest` on the Pi.
