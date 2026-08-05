# Agent notes

## Cursor Cloud specific instructions

Single-service Python/Flask app (`cisco_multi_monitor.py`). See `README.md` for setup, config, and usage.

### Run

```bash
python3 cisco_multi_monitor.py
```

Dashboard: `http://127.0.0.1:5000` — default login `admin` / `admin123` (change before any real use).

### Lint / test / build

- No package manager project file, linter config, or automated test suite in this repo.
- Smoke check: `python3 -m py_compile cisco_multi_monitor.py`
- Functional check: start the app, POST login, add a device via `/api/devices`, then poll `/api/status`.

### Gotchas

- Dependencies are in `requirements.txt`: **Flask** (dashboard) and **Paramiko** (SSH devices only; Telnet-only setups run without it).
- Transport is per device: `transport` field in `devices.json`, defaulting to SSH for port 22 and Telnet otherwise (`resolve_transport`). `ssh_client.py` wraps a Paramiko shell in the same read/write interface as the Telnet client, so `monitor_device` is transport-agnostic.
- Connection failures are summarised by `describe_error` into `device_status[name]["last_error"]` and shown on the device card; keep that mapping in sync when adding transports.
- Vendor matters as much as transport. Cisco IOS streams via `terminal monitor`; FortiOS polls `execute log display`; Junos uses `monitor start messages` with a `show log messages` poll fallback. Vendor comes from the `vendor` field or `detect_vendor` (FortiGate prompt / `user@host>` Junos prompt / default Cisco).
- `ssh_client.read_ssh_banner` **sends the client identification first** — FortiGate and some other appliances wait for that and otherwise look like "no banner". `diagnose_ssh` still continues to a full Paramiko handshake if the raw banner probe fails, and pauses between attempts to avoid appliance rate limits.
- `teraterm_launcher.py` starts Tera Term **on the machine running the dashboard** (normally the operator's PC). It prefers a temporary `.ttl` macro over a command line so the password stays out of the Windows process list. When Tera Term is absent the API returns the macro instead of failing, so a remotely-hosted dashboard still works. `detect()` reports every path it checked (install dirs, registry `App Paths`, globs, `PATH`) and backs the header badge and `/api/teraterm-status`.
- Reconnects use exponential backoff and stop after `MAX_CONNECT_FAILURES`. This is deliberate: FortiGate and similar appliances lock an admin account after a few failed logins, so an endless retry loop turns a small problem into an outage. A stopped device needs an explicit Connect.
- Paramiko identifies itself as `SSH_CLIENT_ID` (default `OpenSSH_8.9`) via `Transport._CLIENT_ID`; some appliances reset connections from unfamiliar client strings.
- The dashboard HTML/JS lives in a plain (non-raw) Python string, so any backslash escape intended for JavaScript must be doubled (`'\\\\n'`). A single `\\n` becomes a real newline and breaks the script with "Invalid or unexpected token".
- `telnetlib` was removed in Python 3.13. `cisco_multi_monitor.py` falls back to the bundled `telnet_client.py`, which implements only the subset used here (`read_until`, `expect`, `read_very_eager`, `write`) and refuses all Telnet option negotiation. This environment has Python 3.12, so the stdlib path is the default — to exercise the fallback, block the import (e.g. a `sys.meta_path` finder raising `ModuleNotFoundError` for `telnetlib`).
- Devices persist in runtime file `devices.json` (inventory). Do not commit it. Import (CSV/JSON) only loads inventory; monitoring starts when the user calls `/api/devices/<name>/monitor` (UI: **Connect & Monitor**).
- Events are in-memory only (lost on restart). Email alerts need real SMTP credentials; dashboard monitoring works without them.
- Full Telnet E2E needs a reachable Cisco (or mock) on the configured host/port. Idle inventory devices do not open Telnet until Connect.
- App binds `0.0.0.0:5000` with `use_reloader=False`; restart the process after code edits.
- `pip --user` installs scripts under `~/.local/bin` — ensure that is on `PATH` if invoking the `flask` CLI (not required to run this app).
- Tera Term (or any other Telnet client) can run in parallel; this app uses a separate VTY session.
