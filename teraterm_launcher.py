#!/usr/bin/env python3
"""
Launch Tera Term on the machine hosting the dashboard.

The dashboard is normally run on the operator's own Windows PC, so clicking
"Tera Term" in the browser can open a real terminal session to the device
using the credentials already stored in the inventory.

Two ways to start a session:

* a `.ttl` macro (default) — keeps the password out of the Windows process
  list, and also handles Telnet auto-login prompts
* direct command line — no temp file, but the password is visible in the
  process list

If Tera Term is not installed on this machine (for example the dashboard runs
on a Linux server), the endpoint returns the macro so the operator can run it
on their own PC instead.
"""

import os
import re
import sys
import shutil
import subprocess
import tempfile
import glob
import platform

try:
    import winreg  # Windows only
except ImportError:
    winreg = None

EXE_NAMES = ["ttermpro.exe", "ttermpro"]

# Common install roots for glob searching (Tera Term 4 and 5, portable copies)
GLOB_PATTERNS = [
    r"C:\Program Files\teraterm*\ttermpro.exe",
    r"C:\Program Files (x86)\teraterm*\ttermpro.exe",
    r"C:\Program Files\Tera Term*\ttermpro.exe",
    r"C:\Program Files (x86)\Tera Term*\ttermpro.exe",
    r"C:\teraterm*\ttermpro.exe",
    r"D:\teraterm*\ttermpro.exe",
    r"C:\tools\teraterm*\ttermpro.exe",
    r"C:\Users\*\AppData\Local\Programs\teraterm*\ttermpro.exe",
]

WINDOWS_PATH_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def is_windows() -> bool:
    return os.name == "nt"


def is_wsl() -> bool:
    if is_windows():
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def host_platform() -> dict:
    """Describe the machine actually running this dashboard."""
    return {
        "system": platform.system(),
        "windows": is_windows(),
        "wsl": is_wsl(),
        "python": sys.version.split()[0],
    }


def path_candidates(path: str) -> list:
    """Variants of a path to try, so Windows paths still work under WSL."""
    path = (path or "").strip().strip('"')
    if not path:
        return []
    candidates = [path]
    if not is_windows():
        match = WINDOWS_PATH_RE.match(path)
        if match:
            drive, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
            candidates.append(f"/mnt/{drive}/{rest}")
            candidates.append(f"/{drive}/{rest}")
    return candidates


def resolve_exe(path: str) -> dict:
    """Check one user-supplied path and explain the outcome."""
    raw = (path or "").strip().strip('"')
    if not raw:
        return {"exe": "", "reason": "empty path"}

    tried = []
    for candidate in path_candidates(raw):
        tried.append(candidate)
        if os.path.isfile(candidate):
            return {"exe": candidate, "reason": "found", "tried": tried}
        if os.path.isdir(candidate):
            inner = os.path.join(candidate, "ttermpro.exe")
            tried.append(inner)
            if os.path.isfile(inner):
                return {"exe": inner, "reason": "found in folder", "tried": tried}
            return {
                "exe": "",
                "reason": "that folder exists but has no ttermpro.exe inside",
                "tried": tried,
            }

    parent = os.path.dirname(tried[0]) if tried else ""
    if parent and os.path.isdir(parent):
        reason = "folder exists but the file does not - check the exact file name"
    elif not is_windows() and WINDOWS_PATH_RE.match(raw):
        reason = (
            f"this dashboard is running on {platform.system()}, so the Windows path "
            f"{raw} does not exist here"
        )
    else:
        reason = "file does not exist on the machine running this dashboard"
    return {"exe": "", "reason": reason, "tried": tried}


def _registry_path() -> str:
    """Tera Term registers itself under App Paths when installed normally."""
    if winreg is None:
        return ""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ttermpro.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\ttermpro.exe",
        ):
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value and os.path.isfile(value):
                        return value
            except OSError:
                continue
    return ""


def detect(explicit: str = "", search_paths=()) -> dict:
    """Locate Tera Term and report everywhere that was checked."""
    searched = []

    if explicit:
        result = resolve_exe(explicit)
        searched.append(f"configured: {explicit} -> {result['reason']}")
        if result["exe"]:
            return _result(result["exe"], searched)

    for path in search_paths:
        for candidate in path_candidates(path):
            searched.append(candidate)
            if os.path.isfile(candidate):
                return _result(candidate, searched)

    reg = _registry_path()
    searched.append("Windows registry (App Paths\\ttermpro.exe)")
    if reg:
        return _result(reg, searched)

    for pattern in GLOB_PATTERNS:
        for candidate in path_candidates(pattern):
            searched.append(candidate)
            matches = sorted(glob.glob(candidate))
            if matches:
                return _result(matches[0], searched)

    for name in EXE_NAMES:
        found = shutil.which(name)
        searched.append(f"PATH: {name}")
        if found:
            return _result(found, searched)

    return {"exe": "", "macro_runner": "", "searched": searched}


def _result(exe: str, searched: list) -> dict:
    return {"exe": exe, "macro_runner": find_macro_runner(exe), "searched": searched}


def find_teraterm(explicit: str = "", search_paths=()) -> str:
    """Locate ttermpro.exe, or return '' when it is not installed here."""
    return detect(explicit, search_paths)["exe"]


def find_macro_runner(teraterm_exe: str) -> str:
    """ttpmacro.exe lives next to ttermpro.exe."""
    if teraterm_exe:
        candidate = os.path.join(os.path.dirname(teraterm_exe), "ttpmacro.exe")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ttpmacro") or shutil.which("ttpmacro.exe") or ""


def _quote(value: str) -> str:
    """Escape single quotes for Tera Term macro string literals."""
    return str(value or "").replace("'", "''")


def build_macro(dev: dict, transport: str) -> str:
    """Build a .ttl macro that connects and logs in."""
    host = _quote(dev.get("host", ""))
    port = int(dev.get("port") or (22 if transport == "ssh" else 23))
    user = _quote(dev.get("username", ""))
    password = _quote(dev.get("password", ""))
    enable = _quote(dev.get("enable_password", ""))

    lines = [
        "; Generated by Cisco Multi-Device Monitor",
        f"; Device: {_quote(dev.get('name', ''))}",
        "",
    ]

    if transport == "ssh":
        lines += [
            f"connect '{host}:{port} /ssh /2 /auth=password /user={user} /passwd={password}'",
        ]
    else:
        lines += [
            f"connect '{host}:{port} /nossh'",
            "wait 'sername:' 'ogin:' 'Password:'",
            f"sendln '{user}'",
            "wait 'assword'",
            f"sendln '{password}'",
        ]

    if enable:
        lines += [
            "wait '>' '#'",
            "sendln 'enable'",
            "wait 'assword'",
            f"sendln '{enable}'",
        ]

    lines.append("")
    return "\r\n".join(lines)


def build_command(dev: dict, transport: str, teraterm_exe: str) -> list:
    """Direct command line (password visible in the process list)."""
    host = dev.get("host", "")
    port = int(dev.get("port") or (22 if transport == "ssh" else 23))
    target = f"{host}:{port}"

    if transport == "ssh":
        return [
            teraterm_exe, target, "/ssh", "/2", "/auth=password",
            f"/user={dev.get('username', '')}",
            f"/passwd={dev.get('password', '')}",
        ]
    return [teraterm_exe, target, "/nossh"]


def launch(dev: dict, transport: str, explicit_exe: str = "",
           search_paths=(), use_macro: bool = True) -> dict:
    """Start Tera Term locally. Returns a result dict for the API."""
    macro = build_macro(dev, transport)
    found = detect(explicit_exe, search_paths)
    teraterm_exe = found["exe"]

    if not teraterm_exe:
        return {
            "ok": False,
            "launched": False,
            "error": "Tera Term (ttermpro.exe) was not found on the machine running this dashboard",
            "macro": macro,
            "searched": found["searched"],
            "hint": (
                "Install Tera Term here, or start the dashboard with "
                "TERATERM_EXE set to the full path of ttermpro.exe, "
                "or download the macro and run it on your PC."
            ),
        }

    runner = found["macro_runner"] if use_macro else ""

    try:
        if runner:
            handle, path = tempfile.mkstemp(prefix="ttl_", suffix=".ttl", text=True)
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(macro)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            command = [runner, path]
        else:
            command = build_command(dev, transport, teraterm_exe)

        subprocess.Popen(command, close_fds=True)
        return {
            "ok": True,
            "launched": True,
            "method": "macro" if runner else "command line",
            "exe": teraterm_exe,
            "command": os.path.basename(command[0]),
        }
    except Exception as e:
        return {
            "ok": False,
            "launched": False,
            "error": f"{e.__class__.__name__}: {e}",
            "exe": teraterm_exe,
            "macro": macro,
        }
