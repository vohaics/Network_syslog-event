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
- `telnetlib` was removed in Python 3.13. `cisco_multi_monitor.py` falls back to the bundled `telnet_client.py`, which implements only the subset used here (`read_until`, `expect`, `read_very_eager`, `write`) and refuses all Telnet option negotiation. This environment has Python 3.12, so the stdlib path is the one exercised by default — to test the fallback, block the import (e.g. a `sys.meta_path` finder raising `ModuleNotFoundError` for `telnetlib`) rather than trusting the stdlib path.
- Devices persist in runtime file `devices.json` (created on first add). Do not commit it.
- Events are in-memory only (lost on restart). Email alerts need real SMTP credentials; dashboard monitoring works without them.
- Full Telnet E2E needs a reachable Cisco (or mock) on the configured host/port. Without a device, the UI/login still works; status stays disconnected and reconnect loops appear in the console.
- App binds `0.0.0.0:5000` with `use_reloader=False`; restart the process after code edits.
- `pip --user` installs scripts under `~/.local/bin` — ensure that is on `PATH` if invoking the `flask` CLI (not required to run this app).
