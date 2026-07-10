# Fermenter ↔ Pi Protokoll (Phase 1)

Einfaches HTTP-JSON-Polling. **Der ESP32 ist Client**, der Pi ist Server.
Der ESP32 pollt alle paar Sekunden; ein Request erledigt beide Richtungen.

## Endpoint

`POST http://<PI_HOST>:<PI_PORT>/api/poll`
Content-Type: `application/json`

### Request (ESP32 → Pi): Telemetrie

```json
{
  "id": "fermenter",
  "core": 30.1,          // DS18B20 Kerntemperatur °C (null bei Sensorfehler)
  "chamber": 24.3,       // BME280 Kammertemperatur °C (null wenn ungültig)
  "rh": 78.5,            // BME280 Luftfeuchte %
  "heater": true,        // Relais an/aus
  "fan": "-TI",          // Lüfter-Statusstring (OFF / R-- / -T- / --I / … / EMG)
  "fan1": 50,            // Lüfter1 PWM %
  "fan2": 50,            // Lüfter2 PWM %
  "core_fault": false,   // Kern-Sensor ausgefallen
  "chamber_alarm": false,// Kammer-Übertemperatur-Notaus aktiv
  "ext": true            // ESP32 ist im External-Modus (Menü)
}
```

### Response (Pi → ESP32): Zeit + Setpoints

```json
{
  "time": 1717430400,    // Pi Unix-Epoch (optional, für ESP32-Uhr/Logs)
  "running": true,       // läuft gerade ein Profil?
  "setpoints": {         // aktuelle Sollwerte (nur angewandt, wenn ESP32 ext_ctrl=ON)
    "heat_enabled": 1, "temp_enabled": 1, "rh_enabled": 1, "idle_enabled": 1,
    "target": 30.0, "dt_lower": 0.5, "dt_floor": 2.0, "dt_ceil": 2.0,
    "temp_lower": 36.0, "temp_upper": 38.0,
    "rh_lower": 65, "rh_upper": 85,
    "temp_pwm_f1": 50, "temp_pwm_f2": 50,
    "rh_pwm_f1": 50, "rh_pwm_f2": 50,
    "idle_pwm_f1": 50, "idle_pwm_f2": 50
  }
}
```

Temperaturen in **°C** (Floats); der ESP32 rechnet intern auf ×10-Ints um.
Felder sind optional — nur mitgeschickte werden angewandt.

**Heizung (Ziel-T-Schema):** `target` = Kern-Zieltemperatur; dazu drei Deltas
(°C, Differenzen): `dt_lower` = Kern-Hysteresenband (heizen unter `target−dt_lower`,
aus bei `target`), `dt_floor` = Kammer-Boden (heizen wenn Kammer ≤ `target−dt_floor`),
`dt_ceil` = Kammer-Deckel (rastende Überschwing-Bremse: sperren wenn Kammer ≥
`target+dt_ceil`, frei bei `target`). Im Profil variiert i. d. R. nur `target` pro
Schritt; die Deltas sind globale Geräte-Konstanten.

## Verhalten / Autonomie

- Der **ESP32 entscheidet über den Modus** (Menü `Ext ctrl`). Nur bei `ext_ctrl=ON`
  werden empfangene Setpoints angewandt — flüchtig (RAM, **kein NVS-Schreiben** →
  kein Flash-Verschleiß). Die lokalen NVS-Werte bleiben unangetastet.
- **Modus AUS** → `cfg.reload()` stellt die lokalen NVS-Werte wieder her.
- **Pi/WLAN weg** → der ESP32 hält die zuletzt empfangenen Setpoints und läuft
  autonom weiter. Alle Fail-Safes (Kammer-Cutoff, Sensor-Fault, Safety-Gate)
  laufen lokal, unabhängig vom Netz.

## Phase 2 (später)

- Kurve + Scheduler auf den ESP32 verlagern, DS3231-RTC → volle Autonomie auch
  über Reboots/Stromausfall.
