#!/usr/bin/env python3
"""
Minimal Telnet client used when the standard library `telnetlib` is missing.

`telnetlib` was deprecated in Python 3.11 and removed in Python 3.13, so this
module provides the small subset of the API this project relies on:

    Telnet(host, port, timeout)
    .read_until(expected, timeout)
    .expect(patterns, timeout)
    .read_very_eager()
    .write(data)
    .close()

Telnet option negotiation is refused (WONT / DONT), which matches the default
behaviour of `telnetlib` when no option callback is registered and is what
Cisco IOS expects from a plain monitoring session.
"""

import re
import select
import socket

IAC = 255   # interpret as command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250    # subnegotiation begin
SE = 240    # subnegotiation end


class Telnet:
    def __init__(self, host, port=23, timeout=None):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.sock = socket.create_connection((host, self.port), timeout)
        self.sock.setblocking(False)
        self.eof = False
        self._raw = bytearray()    # bytes not yet scanned for IAC sequences
        self._buf = bytearray()    # decoded application data

    # -------------------- internals --------------------
    def _refuse(self, cmd, option):
        """Refuse any option the peer offers or requests."""
        if cmd == DO:
            reply = bytes([IAC, WONT, option])
        elif cmd == WILL:
            reply = bytes([IAC, DONT, option])
        else:
            return
        try:
            self.sock.sendall(reply)
        except OSError:
            pass

    def _parse(self):
        """Move raw bytes into the data buffer, handling IAC sequences."""
        raw = self._raw
        out = bytearray()
        i, n = 0, len(raw)

        while i < n:
            byte = raw[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue

            if i + 1 >= n:
                break                      # incomplete command, wait for more
            cmd = raw[i + 1]

            if cmd == IAC:                 # escaped 0xFF
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    break
                self._refuse(cmd, raw[i + 2])
                i += 3
            elif cmd == SB:
                end = raw.find(bytes([IAC, SE]), i + 2)
                if end == -1:
                    break
                i = end + 2
            else:                          # NOP, GA and other 2-byte commands
                i += 2

        del raw[:i]
        self._buf += out

    def _fill(self, timeout=0):
        """Read whatever is available. Returns True if new data arrived."""
        if self.eof:
            return False
        try:
            ready, _, _ = select.select([self.sock], [], [], timeout)
        except (OSError, ValueError):
            self.eof = True
            return False
        if not ready:
            return False
        try:
            chunk = self.sock.recv(4096)
        except BlockingIOError:
            return False
        except OSError:
            self.eof = True
            return False
        if not chunk:
            self.eof = True
            return False
        self._raw += chunk
        self._parse()
        return True

    def _take(self, count):
        data = bytes(self._buf[:count])
        del self._buf[:count]
        return data

    # -------------------- public API --------------------
    def read_until(self, expected, timeout=None):
        """Read until `expected` is seen. Returns what was read (may be partial)."""
        deadline = None if timeout is None else _now() + timeout
        while True:
            index = self._buf.find(expected)
            if index != -1:
                return self._take(index + len(expected))
            if self.eof:
                return self._take(len(self._buf))
            wait = 0.2 if deadline is None else min(0.2, deadline - _now())
            if wait <= 0:
                return self._take(len(self._buf))
            self._fill(wait)

    def expect(self, patterns, timeout=None):
        """Wait for the first matching pattern. Returns (index, match, text)."""
        compiled = [p if hasattr(p, "search") else re.compile(re.escape(p)) for p in patterns]
        deadline = None if timeout is None else _now() + timeout
        while True:
            for index, pattern in enumerate(compiled):
                match = pattern.search(bytes(self._buf))
                if match:
                    return index, match, self._take(match.end())
            if self.eof:
                return -1, None, self._take(len(self._buf))
            wait = 0.2 if deadline is None else min(0.2, deadline - _now())
            if wait <= 0:
                return -1, None, self._take(len(self._buf))
            self._fill(wait)

    def read_very_eager(self):
        """Return everything readable right now without blocking."""
        while self._fill(0):
            pass
        return self._take(len(self._buf))

    def write(self, buffer):
        if IAC in buffer:
            buffer = buffer.replace(bytes([IAC]), bytes([IAC, IAC]))
        self.sock.sendall(buffer)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def _now():
    import time
    return time.monotonic()
