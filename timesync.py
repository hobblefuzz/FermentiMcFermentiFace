"""SNTP time sync — sets the RTC from an NTP server, watchdog-safe.

A minimal SNTP client with a hard socket timeout, so a missing or slow server
can never stall the main loop.  Tries the local router (gateway) first — fast
and works without internet — then a public pool as a fallback.

After a successful sync the RTC holds wall-clock time; unix_time() returns it as
a Unix epoch (matching the Pi's start_epoch) once synced, else None.
"""

import socket
import struct
import time

_NTP_DELTA   = 3155673600   # 1900-01-01 → 2000-01-01 (MicroPython epoch), seconds
_UNIX_OFFSET = 946684800    # 2000-01-01 → 1970-01-01 (Unix epoch), seconds


def _query(host, timeout):
    """One SNTP request; return MicroPython-epoch seconds, or None on failure."""
    addr = socket.getaddrinfo(host, 123)[0][-1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        pkt = bytearray(48)
        pkt[0] = 0x1B                       # LI=0, VN=3, Mode=3 (client)
        s.sendto(pkt, addr)
        msg = s.recv(48)
    finally:
        s.close()
    if not msg or len(msg) < 44:
        return None
    secs = struct.unpack("!I", msg[40:44])[0]   # transmit timestamp (seconds)
    secs -= _NTP_DELTA
    return secs if secs > 0 else None


class TimeSync:
    def __init__(self, timeout=1.0):
        self._timeout = timeout
        self.synced   = False

    def sync(self, hosts):
        """Try each host once; on the first success set the RTC, return True."""
        import machine                      # MicroPython-only; local so the module
        for host in hosts:                  # stays importable/testable on CPython
            if not host:
                continue
            try:
                t = _query(host, self._timeout)
            except Exception:
                t = None
            if t is not None:
                tm = time.gmtime(t)
                # RTC datetime: (year, month, day, weekday, hour, min, sec, subsec)
                machine.RTC().datetime(
                    (tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
                self.synced = True
                return True
        return False

    def unix_time(self):
        """Wall-clock as a Unix epoch, or None if never synced."""
        return time.time() + _UNIX_OFFSET if self.synced else None
