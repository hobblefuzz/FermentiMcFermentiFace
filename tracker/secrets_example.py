# Copy to secrets.py on the tracker ESP32 and fill in. secrets.py is gitignored.

WIFI_SSID = ""
WIFI_PASS = ""

PI_HOST = "192.168.1.50"     # Raspberry Pi IP (use the IP, not a hostname)
PI_PORT = 8080

# ── Fast-connect options (optional but recommended for battery life) ──────────
# Static IP skips DHCP.  Tuple: (ip, subnet, gateway, dns).  None = use DHCP.
STATIC_IP = None
# STATIC_IP = ("192.168.1.60", "255.255.255.0", "192.168.1.1", "192.168.1.1")

# Pin the access point's MAC (BSSID) to skip the channel scan. None = scan.
# Find it once with:  network.WLAN(network.STA_IF).scan()
BSSID = None
# BSSID = b"\x00\x11\x22\x33\x44\x55"
