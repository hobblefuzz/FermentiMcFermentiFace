# Hardware pin assignments — adjust to match your wiring
# All GPIO numbers refer to ESP32 GPIO numbering.

# I2C bus (shared by BME280 and 1602 LCD)
I2C_SDA = 21
I2C_SCL = 22
I2C_FREQ = 100_000

# I2C device addresses
LCD_I2C_ADDR  = 0x27   # PCF8574 backpack — try 0x3F if no display
BME280_I2C_ADDR = 0x76  # try 0x77 if sensor not found

# Rotary encoder
ENC_CLK = 32   # CLK / A pin
ENC_DT  = 33   # DT  / B pin
ENC_SW  = 25   # Push button (active-low, internal pull-up)

# PWM fans — placed on the opposite side of the dev board from the encoder
# (GPIO 25/32/33) to minimise capacitive EMI coupling onto the encoder lines.
FAN1_PIN  = 18   # Ventilation fan (intake / Belüftung)
FAN2_PIN  = 19   # Exhaust fan     (Abluft)
FAN_PWM_FREQ = 100   # 100 Hz — works for 2-wire DC fans without dedicated PWM input
                     # (24 kHz would be inaudible but only suits 4-wire PWM fans)

# DS18B20 core-temperature sensor (1-Wire)
ONEWIRE_PIN = 4    # data line — needs a 4.7 kΩ pull-up to 3.3 V
                   # (one resistor regardless of how many sensors share the bus)

# Heating relay
RELAY_PIN = 23
RELAY_ACTIVE_LOW = False   # False = active-high (LOW pin = relay off, per the
                           #         module's manual / "active-on" example).
                           # Set True if your module energises on a LOW input.

# LCD size
LCD_COLS  = 16
LCD_LINES = 2
