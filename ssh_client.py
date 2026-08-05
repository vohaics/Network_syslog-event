#!/usr/bin/env python3
"""
SSH session support for devices that do not allow Telnet (port 22).

Wraps a Paramiko interactive shell in the same small interface the monitor
loop uses for Telnet (`read_very_eager`, `write`, `close`), so the syslog
matching logic stays identical for both transports.

Requires: pip install paramiko
"""

import re
import time

try:
    import paramiko
except ImportError:                        # reported clearly when a device needs SSH
    paramiko = None

# Older Cisco IOS often only offers legacy key exchange / ciphers that modern
# Paramiko disables by default. These are appended to Paramiko's preferences so
# such devices still negotiate instead of failing with "no matching ..." errors.
LEGACY_KEX = [
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group-exchange-sha1",
    "diffie-hellman-group1-sha1",
]
LEGACY_CIPHERS = ["aes128-cbc", "aes192-cbc", "aes256-cbc", "3des-cbc"]
LEGACY_KEYS = ["ssh-rsa"]


class SSHSession:
    """Interactive SSH shell with a Telnet-like read/write interface."""

    def __init__(self, channel, client):
        self.channel = channel
        self.client = client
        self._buf = bytearray()

    def _drain(self):
        while self.channel.recv_ready():
            chunk = self.channel.recv(4096)
            if not chunk:
                break
            self._buf += chunk

    def read_very_eager(self) -> bytes:
        self._drain()
        data = bytes(self._buf)
        self._buf.clear()
        return data

    def read_until(self, expected: bytes, timeout: float = 8) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain()
            index = self._buf.find(expected)
            if index != -1:
                end = index + len(expected)
                data = bytes(self._buf[:end])
                del self._buf[:end]
                return data
            time.sleep(0.15)
        data = bytes(self._buf)
        self._buf.clear()
        return data

    def expect(self, patterns, timeout: float = 8):
        compiled = [p if hasattr(p, "search") else re.compile(re.escape(p)) for p in patterns]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain()
            for index, pattern in enumerate(compiled):
                match = pattern.search(bytes(self._buf))
                if match:
                    data = bytes(self._buf[:match.end()])
                    del self._buf[:match.end()]
                    return index, match, data
            time.sleep(0.15)
        data = bytes(self._buf)
        self._buf.clear()
        return -1, None, data

    def write(self, data: bytes):
        self.channel.sendall(data)

    def close(self):
        for closer in (self.channel.close, self.client.close):
            try:
                closer()
            except Exception:
                pass


def _tune_legacy_algorithms():
    """Allow legacy KEX/ciphers used by older Cisco IOS images."""
    transport = paramiko.Transport
    for attr, extra in (
        ("_preferred_kex", LEGACY_KEX),
        ("_preferred_ciphers", LEGACY_CIPHERS),
        ("_preferred_keys", LEGACY_KEYS),
    ):
        current = list(getattr(transport, attr, ()))
        for item in extra:
            if item not in current:
                current.append(item)
        setattr(transport, attr, tuple(current))


def open_ssh_session(dev: dict, timeout: float = 15) -> SSHSession:
    """Log in over SSH, enter enable mode if needed, and turn on terminal monitor."""
    if paramiko is None:
        raise RuntimeError(
            "SSH requires Paramiko. Install it with: pip install paramiko"
        )

    _tune_legacy_algorithms()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=dev["host"],
        port=int(dev.get("port", 22)),
        username=dev["username"],
        password=dev["password"],
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )

    channel = client.invoke_shell(width=200, height=1000)
    channel.settimeout(0.0)
    session = SSHSession(channel, client)

    # Some devices show a banner or "Password:" prompt inside the shell.
    index, _, _ = session.expect([b"#", b">"], timeout=10)
    if index == 1 and dev.get("enable_password"):
        session.write(b"enable\n")
        session.read_until(b"assword", timeout=5)
        session.write(dev["enable_password"].encode() + b"\n")
        session.expect([b"#"], timeout=8)

    session.write(b"terminal length 0\n")
    session.write(b"terminal monitor\n")
    session.write(b"\n")
    time.sleep(0.4)
    session.read_very_eager()
    return session
