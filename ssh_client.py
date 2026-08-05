#!/usr/bin/env python3
"""
SSH session support for network devices (Cisco IOS, FortiOS, Junos).

Wraps a Paramiko interactive shell in the same small interface the monitor
loop uses for Telnet (`read_very_eager`, `write`, `close`).

Requires: pip install paramiko
"""

import re
import socket
import time
import os

try:
    import paramiko
except ImportError:
    paramiko = None

# Older network gear often only offers legacy key exchange / ciphers that modern
# Paramiko disables by default. Only names this Paramiko build actually
# implements are added — advertising unimplemented ones breaks the handshake.
LEGACY_KEX = [
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group-exchange-sha1",
    "diffie-hellman-group1-sha1",
]
LEGACY_CIPHERS = ["aes128-cbc", "aes192-cbc", "aes256-cbc", "3des-cbc"]
LEGACY_KEYS = ["ssh-rsa"]

CLIENT_ID = b"SSH-2.0-OpenSSH_8.9\r\n"

# Some appliances (FortiGate in particular) are picky about unknown client
# identification strings. Presenting a common OpenSSH string avoids resets.
SSH_CLIENT_ID = os.environ.get("SSH_CLIENT_ID", "OpenSSH_8.9")


class SSHSession:
    """Interactive SSH shell with a Telnet-like read/write interface."""

    def __init__(self, channel, client, remote_version=""):
        self.channel = channel
        self.client = client
        self.remote_version = remote_version or ""
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
    """Enable legacy KEX/ciphers used by older network gear."""
    transport = paramiko.Transport
    supported = (
        ("_preferred_kex", LEGACY_KEX, getattr(transport, "_kex_info", {})),
        ("_preferred_ciphers", LEGACY_CIPHERS, getattr(transport, "_cipher_info", {})),
        ("_preferred_keys", LEGACY_KEYS, getattr(transport, "_key_info", {})),
    )
    for attr, extra, known in supported:
        current = list(getattr(transport, attr, ()))
        for item in extra:
            if item in known and item not in current:
                current.append(item)
        setattr(transport, attr, tuple(current))

    # Identify as OpenSSH; appliances sometimes reset unfamiliar clients.
    if SSH_CLIENT_ID:
        transport._CLIENT_ID = SSH_CLIENT_ID


def open_ssh_shell(dev: dict, timeout: float = 25) -> SSHSession:
    """Log in over SSH and return the shell without sending any vendor commands."""
    if paramiko is None:
        raise RuntimeError("SSH requires Paramiko. Install it with: pip install paramiko")

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
        compress=False,
    )
    channel = client.invoke_shell(term="vt100", width=200, height=1000)
    channel.settimeout(0.0)

    remote = ""
    try:
        remote = client.get_transport().remote_version or ""
    except Exception:
        pass
    return SSHSession(channel, client, remote_version=remote)


# Kept for callers that still import the old name.
def open_ssh_session(dev: dict, timeout: float = 25) -> SSHSession:
    return open_ssh_shell(dev, timeout=timeout)


def read_ssh_banner(host: str, port: int = 22, timeout: float = 10) -> str:
    """Read the SSH identification string.

    Many appliances (FortiGate, some Juniper) wait for the *client* ID before
    sending their own banner. Tera Term always sends first; a bare recv()
    times out and falsely reports "no banner".
    """
    with socket.create_connection((host, int(port)), timeout) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendall(CLIENT_ID)
        except OSError:
            pass
        data = b""
        deadline = time.monotonic() + timeout
        while b"\n" not in data and len(data) < 512 and time.monotonic() < deadline:
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
    return data.decode("utf-8", errors="replace").strip()


def diagnose_ssh(dev: dict) -> list:
    """Step-by-step SSH check used by the dashboard's Test button.

    Does NOT abort after a raw-banner failure — Paramiko is still tried,
    because that is what the monitor itself uses and some devices only answer
    after a full client handshake.
    """
    steps = []
    host, port = dev["host"], int(dev.get("port", 22))

    try:
        with socket.create_connection((host, port), 8):
            pass
        steps.append({"step": f"TCP connect to {host}:{port}", "ok": True, "detail": "open"})
    except Exception as e:
        steps.append({"step": f"TCP connect to {host}:{port}", "ok": False, "detail": str(e)})
        return steps

    # Brief pause so appliances with connection rate limits recover
    # (FortiGate often needs this after a rapid open/close).
    time.sleep(0.6)

    try:
        banner = read_ssh_banner(host, port, timeout=10)
        looks_like_ssh = banner.startswith("SSH-")
        steps.append({
            "step": "SSH banner (raw)",
            "ok": looks_like_ssh,
            "detail": banner or "(no banner — will still try full Paramiko handshake)",
        })
    except Exception as e:
        steps.append({
            "step": "SSH banner (raw)",
            "ok": False,
            "detail": f"{e} — will still try full Paramiko handshake",
        })

    if paramiko is None:
        steps.append({"step": "Paramiko installed", "ok": False,
                      "detail": "pip install paramiko"})
        return steps

    time.sleep(0.6)
    session = None
    try:
        session = open_ssh_shell(dev, timeout=25)
        remote = session.remote_version or "(unknown)"
        steps.append({"step": "SSH handshake (Paramiko)", "ok": True,
                      "detail": f"server={remote}"})
        steps.append({"step": "Authentication", "ok": True, "detail": "accepted"})
        session.write(b"\n")
        time.sleep(1.5)
        prompt = session.read_very_eager().decode(errors="ignore").strip()
        # Clear ANSI / control chars for display
        prompt_clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", prompt)
        steps.append({
            "step": "Shell prompt",
            "ok": bool(prompt_clean),
            "detail": prompt_clean[-240:] or "(no output — check vendor / privilege)",
        })
    except Exception as e:
        steps.append({
            "step": "SSH handshake / auth / shell",
            "ok": False,
            "detail": f"{e.__class__.__name__}: {e}",
        })
    finally:
        if session:
            session.close()
    return steps
