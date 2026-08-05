# Cisco Multi-Device Monitor

Lightweight Python tool for monitoring Cisco devices **without changing any configuration on the devices**.

It keeps persistent Telnet sessions, enables `terminal monitor`, watches for critical syslog events (interface down, BGP neighbor down, OSPF neighbor down), sends **email alerts**, and provides a modern web dashboard.

---

## Features

| Feature | Description |
|---------|-------------|
| Import network list | Upload CSV/JSON with IP + username/password into inventory |
| Search + on-demand connect | Search inventory, then **Connect & Monitor** only when needed |
| Multi-device | Monitor many Cisco routers/switches at the same time |
| Add / Remove devices | From the web UI – no need to edit code |
| Persistent storage | Devices saved in `devices.json` |
| Email alerts | Instant email when a matching event occurs |
| Sound notification | Browser beep (can be toggled ON/OFF) |
| Event filter | Filter the event table by device |
| Export CSV | Download all events as CSV |
| Login protection | Simple username/password for the dashboard |
| Auto-reconnect | Automatically reconnects if the Telnet session drops |
| Keepalive | Prevents device `exec-timeout` |

---

## Requirements

- Python 3.8 or higher (**including 3.13+**)
- Flask
- Paramiko (only if you monitor devices over **SSH**)

```bash
pip install -r requirements.txt
```

(`smtplib`, `json`, `csv` are part of the Python standard library.)

### Python 3.13 and newer

`telnetlib` was removed from the standard library in Python 3.13. The bundled
`telnet_client.py` is used automatically in that case, so keep it next to
`cisco_multi_monitor.py`. No extra install is needed.

---

## Quick Start

1. Download the files:
   - `cisco_multi_monitor.py`
   - `telnet_client.py` (needed on Python 3.13+)
   - `README.md` (this file)

2. Edit the configuration section at the top of `cisco_multi_monitor.py`:

```python
# Dashboard login (CHANGE THESE!)
DASHBOARD_USER = "admin"
DASHBOARD_PASS = "admin123"

# Email settings
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = "your_email@gmail.com"
EMAIL_PASS = "your_app_password"      # Gmail App Password
EMAIL_TO   = "alert@yourdomain.com"

# Also change this secret key
app.secret_key = "change-this-to-a-long-random-string-please-32chars-min"
```

3. Run the tool:

```bash
python cisco_multi_monitor.py
```

On Windows you can also just double-click **`run.bat`** (it installs Flask if missing).

4. Open your browser:

```
http://YOUR_SERVER_IP:5000
```

5. Login with the credentials you set, then **import your network list** (or add devices one-by-one).
6. Use the search box to find a device, click **Connect & Monitor** to open Telnet + `terminal monitor` and watch syslog events.

---

## Import Network List (CSV / JSON)

Inventory is stored in `devices.json`. Importing does **not** connect yet — search and click **Connect & Monitor** when you need syslog watching.

### CSV (recommended)

Header row required. Column names are flexible (`host`/`ip`, `username`/`user`, `password`/`pass`, etc.):

```csv
name,host,port,username,password,enable_password
Core-Router,192.168.1.1,23,admin,cisco123,
Edge-Switch,192.168.1.2,23,admin,cisco123,enablepass
```

Upload via the dashboard **Import Network List** section, or:

```bash
curl -b cookies.txt -F "file=@networklist.csv" http://YOUR_SERVER:5000/api/devices/import
```

A sample file is also available from the dashboard: **Sample CSV**.

### JSON

```json
[
  {
    "name": "Core-Router",
    "host": "192.168.1.1",
    "port": 23,
    "username": "admin",
    "password": "cisco123",
    "enable_password": ""
  }
]
```

---

## Telnet or SSH

Both are supported. Pick the protocol in the add-device form, or let the port decide:

| Port | Protocol used |
|------|---------------|
| 23 | Telnet |
| 22 | SSH (needs `pip install paramiko`) |

If Tera Term reaches a device on **port 22**, that device is SSH-only — set its protocol to **SSH** here too. Choosing Telnet against port 22 leaves the card in *Reconnecting…*, and the card now says exactly that.

CSV import accepts a `transport` (or `protocol`) column:

```csv
name,host,transport,port,username,password,enable_password
Core-Router,192.168.1.1,telnet,23,admin,cisco123,
SSH-Router,10.136.110.254,ssh,22,admin,secret,
```

Older IOS images that only offer legacy SSH key exchange and ciphers are handled automatically.

## Supported platforms

| Vendor | How events are collected | Default transport |
|--------|--------------------------|-------------------|
| **Cisco IOS / IOS-XE** | `terminal monitor` stream | Telnet or SSH |
| **FortiGate (FortiOS)** | Poll `execute log display` every 20s | SSH |
| **Juniper Junos** | `monitor start messages` stream (falls back to `show log messages`) | SSH |

Vendor is auto-detected from the CLI prompt (`Router#`, `FortiGate-100F #`, `admin@host>`), or set explicitly in the form / CSV `vendor` column (`cisco`, `fortios`, `junos`).

### SSH "no banner received" (common on FortiGate)

Some appliances **wait for the client identification** before sending their own SSH banner. Tera Term always sends first, so it works; a bare `recv()` times out. The monitor now:

1. Sends the client ID before reading the banner
2. Still tries a full Paramiko handshake even if the raw banner probe is empty
3. Waits briefly between connection attempts (FortiGate rate-limits rapid reconnects)

Also close extra Tera Term windows when testing — FortiGate SSH session limits are often low (2–3).

### FortiGate notes

No `terminal monitor`. Memory/disk logging must be enabled (default) for `execute log display`. Polling is ~20s granularity, not instant.

### Juniper notes

Stays in operational mode (`user@host>`). Never enters configuration mode. Needs permission for `monitor start messages` or at least `show log messages`.

### Why a device is not connecting

Press **Test** on any device card. It reports each stage separately:

```
OK   — TCP connect to 10.136.110.254:22: open
OK   — SSH banner (raw): SSH-2.0-FortiSSH_7.4
OK   — SSH handshake (Paramiko): server=SSH-2.0-FortiSSH_7.4
OK   — Authentication: accepted
OK   — Shell prompt: FortiGate-100F #
```

| Error shown | Meaning |
|-------------|---------|
| `Port 22 is SSH - change this device's protocol to SSH` | Telnet selected for an SSH port |
| `Authentication failed - check username / password` | Wrong credentials |
| `SSH banner timeout...` | Appliance waited for client ID / rate-limit / session full — click Test again after closing Tera Term |
| `Device reset the connection...` | Admin lockout, all SSH admin sessions in use, or a trusted-host/local-in policy |
| `stopped after N failed attempts` | Retrying was halted on purpose so the account is not locked out — fix the cause, then press Connect again |
| `Timed out - host unreachable or SSH blocked` | Network/ACL problem |
| `SSH needs Paramiko: pip install paramiko` | Missing dependency |

---

## Tera Term and this tool

**Yes — using Tera Term is OK, and the dashboard can launch it for you.**

### Click to connect

Every device card has a **Tera Term** button, and every row in **Recent Events** has a
**Connect** column. Clicking a button opens Tera Term on the PC running this dashboard,
connecting to that device with the credentials already stored in the inventory
(SSH or Telnet, whichever the device uses).

Event rows show one button per target:

* the device that reported the event
* any **IP address found inside the log message** that also exists in your inventory
  (for example the BGP/OSPF neighbour that just went down)

So when an event arrives mentioning an IP, you can jump straight into a CLI session
on that IP without typing the address or password.

### How the session is started

By default a temporary `.ttl` macro is generated and run with `ttpmacro.exe`. This
keeps the password out of the Windows process list and also handles Telnet
username/password prompts and `enable` automatically. Set
`TERATERM_USE_MACRO = False` in the script to use a plain command line instead.

Tera Term is found automatically in the usual install folders. If yours is elsewhere:

```powershell
set TERATERM_EXE=D:\tools\teraterm\ttermpro.exe
python cisco_multi_monitor.py
```

### Set the Tera Term path in the UI (per PC)

Each PC can have Tera Term installed in a different folder. Click **Settings**
(or the **Tera Term** badge in the header) and paste the full path to
`ttermpro.exe`, then **Save**. It is stored in `settings.json` on that machine.

```
C:\Program Files (x86)\teraterm\ttermpro.exe
```

**Detect** tries the usual install folders / registry / PATH and fills the field
for you — still click **Save** to keep it. Clear the field and Save to go back
to auto-detect.

Priority: UI Settings → `TERATERM_EXE` environment variable → auto-detect.

### "Tera Term not found" even though the path is correct

Click **Detect** with your path in the box — it now checks that exact path and
says why it failed:

| Message | Meaning |
|---------|---------|
| `folder exists but the file does not` | Wrong file name in that folder |
| `that folder exists but has no ttermpro.exe inside` | You pointed at the wrong folder |
| `this dashboard is running on Linux/…, so the Windows path … does not exist here` | **The dashboard is not running on your Windows PC** |

That last one is the common surprise. Tera Term is launched by the machine
**hosting the dashboard**, not by your browser. If you start
`cisco_multi_monitor.py` inside WSL, a container, or on a server, it cannot see
`C:\...` or start Windows programs — even though the browser shows
`127.0.0.1:5000`.

Fixes:

* run `python cisco_multi_monitor.py` in a normal **Windows** Command Prompt / PowerShell, **or**
* keep it where it is and use the **.ttl macro download** to open Tera Term yourself

Windows paths are translated automatically when running under WSL
(`C:\dir\file` → `/mnt/c/dir/file`), so a WSL-hosted dashboard can still find
the file — but it still cannot launch a Windows GUI program reliably.

You can also paste the **folder** instead of the full file path; `ttermpro.exe`
is appended for you.

### If the dashboard runs on a different machine

The launch happens on whichever machine runs the dashboard. When Tera Term is not
installed there, the button offers to **download the `.ttl` macro** instead — save it
and double-click it on your own PC. You can also fetch it directly:

```
/api/teraterm/<device-name-or-ip>/macro
```

### Notes

- This monitor opens its **own** session (separate from Tera Term), over Telnet or SSH.
- You can keep using Tera Term for manual CLI work while the dashboard monitors syslog.
- Cisco / FortiGate / Juniper all have session limits; if you hit “no more connections”, free a session or raise the limit.
- Only devices in the inventory can be launched — an unknown IP returns a clear error,
  because no credentials are stored for it.

---

## Gmail App Password

If you use Gmail:

1. Enable 2-Step Verification on your Google account.
2. Go to **Google Account → Security → App passwords**.
3. Create a new app password and use it as `EMAIL_PASS`.

---

## How It Works

Because you do **not** have permission to configure the Cisco device (no `logging host`), the tool:

1. Opens a Telnet session to the device.
2. Logs in (and enters enable mode if needed).
3. Sends `terminal monitor` so syslog messages appear in this session.
4. Continuously reads the output.
5. Matches lines against the configured patterns.
6. Sends an email and updates the dashboard when a match is found.
7. Sends periodic keepalives (`\n`) to prevent the device from timing out the session.
8. Automatically reconnects if the connection is lost.

---

## Detected Events (default patterns)

```
%LINEPROTO-5-UPDOWN ... changed state to down
%LINK-3-UPDOWN ... changed state to down
%BGP-5-ADJCHANGE ... Down
%BGP_SESSION-5-ADJCHANGE ... removed from session
%OSPF-5-ADJCHG ... from FULL to DOWN
%OSPF-5-ADJCHG ... Neighbor Down
```

You can add or remove patterns in the `PATTERNS` list inside the script.

---

## Adding Devices

### Import network list (recommended for many devices)

1. Login to the dashboard.
2. Upload your CSV/JSON under **Import Network List**.
3. Search for a device → click **Connect & Monitor**.
4. Click **Stop** when you no longer need that session.

### Add one device from the Web UI

1. Fill in the **Add Single Device** form (name, IP, port, username, password, optional enable).
2. Click **+ Add Device** (saved to inventory as idle).
3. Click **Connect & Monitor** when you want syslog watching.

### Manually edit `devices.json`

```json
[
  {
    "name": "Core-Router",
    "host": "192.168.1.1",
    "port": 23,
    "username": "admin",
    "password": "cisco123",
    "enable_password": ""
  }
]
```

Restart the script after manual edits. Devices load as idle until you connect from the UI.

---

## Dashboard Features

- Live status cards for every device (green = connected, red = disconnected)
- Last keepalive time and last event time
- Event table with device tag
- Filter events by device
- Sound ON/OFF toggle (preference saved in browser)
- Export all events to CSV
- Logout button

---

## Files Created at Runtime

| File | Purpose |
|------|---------|
| `devices.json` | Stores the list of devices |
| (none other) | Events are kept in memory (last 500) |

---

## Running as a Service (Linux)

Example systemd unit (`/etc/systemd/system/cisco-monitor.service`):

```ini
[Unit]
Description=Cisco Multi-Device Monitor
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/cisco-monitor
ExecStart=/usr/bin/python3 /opt/cisco-monitor/cisco_multi_monitor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cisco-monitor
```

---

## Limitations & Notes

- Uses **Telnet** only (not SSH). If your devices only allow SSH, the script needs to be adapted (e.g. with `paramiko` or `netmiko`).
- Requires that the Cisco device still generates the syslog messages to the monitor session (normal default behavior).
- Device `exec-timeout` should not be extremely short; the keepalive interval is 50 seconds by default.
- This tool does **not** modify any configuration on the Cisco devices.
- For production use, change the default login credentials and `secret_key`.

---

## Troubleshooting

| Problem | Possible cause / solution |
|---------|---------------------------|
| `ModuleNotFoundError: No module named 'telnetlib'` | Python 3.13+ removed `telnetlib`. Make sure `telnet_client.py` sits in the same folder as `cisco_multi_monitor.py` (it is used automatically) |
| Stuck on *Reconnecting…* but Tera Term logs in fine | Tera Term is probably using **SSH (port 22)**. Set the device protocol to **SSH**. Read the card's Error line |
| `SSH requires Paramiko` | Run `pip install paramiko` |
| Cannot connect | Check IP, port, username, password, and that Telnet/SSH is allowed |
| Session drops often | Lower `KEEPALIVE_INTERVAL` or check device `exec-timeout` |
| No email received | Check SMTP settings and Gmail App Password |
| No events appear | Confirm the device is generating the expected syslog messages (`terminal monitor` works when you login manually) |
| Sound does not play | Browser may block autoplay – click the Sound button once |

---

## License

Free to use and modify for personal or internal network operations use.
