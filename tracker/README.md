# Battery Temperature Tracker

A second, standalone ESP32 that just tracks one temperature (e.g. Moromi in the
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

## Battery life

Dominated by wake time × wake current. Realistic wake ≈ 2–4 s at ~250 mA; at a
30-min interval a 2000 mAh cell lasts **weeks to months** (self-discharge often
limits before consumption does).

⚠️ **Deep-sleep current depends heavily on the board.** A plain ESP32 DevKit can
draw several mA in deep sleep (onboard USB-serial + regulator), which wrecks the
estimate. Low-power boards (TinyPICO, LOLIN32, Adafruit Feather ESP32, …) reach
~10–100 µA. Measure yours.

## Testing

To test without the 30-min wait, temporarily comment out the last
`machine.deepsleep(...)` line so it runs, prints, and stops — then check the
Moromi tab / `GET /api/track/latest` on the Pi.
