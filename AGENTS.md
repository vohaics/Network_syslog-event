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

- **Flask** is the only third-party dependency (`pip3 install --user flask`). Stdlib `telnetlib` is used and is **deprecated** (removed in Python 3.13); this environment uses Python 3.12.
- Devices persist in runtime file `devices.json` (inventory). Do not commit it. Import (CSV/JSON) only loads inventory; monitoring starts when the user calls `/api/devices/<name>/monitor` (UI: **Connect & Monitor**).
- Events are in-memory only (lost on restart). Email alerts need real SMTP credentials; dashboard monitoring works without them.
- Full Telnet E2E needs a reachable Cisco (or mock) on the configured host/port. Idle inventory devices do not open Telnet until Connect.
- App binds `0.0.0.0:5000` with `use_reloader=False`; restart the process after code edits.
- `pip --user` installs scripts under `~/.local/bin` — ensure that is on `PATH` if invoking the `flask` CLI (not required to run this app).
- Tera Term (or any other Telnet client) can run in parallel; this app uses a separate VTY session.
