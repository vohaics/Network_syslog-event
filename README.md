# Cisco Multi-Device Monitor

Lightweight Python tool for monitoring Cisco devices **without changing any configuration on the devices**.

It keeps persistent Telnet sessions, enables `terminal monitor`, watches for critical syslog events (interface down, BGP neighbor down, OSPF neighbor down), sends **email alerts**, and provides a modern web dashboard.

---

## Features

| Feature | Description |
|---------|-------------|
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
`cisco_multi_monitor.py`:

```
cisco_multi_monitor.py
telnet_client.py
```

No extra install is needed.

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

5. Login with the credentials you set, then add your Cisco devices using the form.

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

### From the Web UI (recommended)

1. Login to the dashboard.
2. Fill in the **Add New Device** form:
   - Name (unique)
   - IP / Hostname
   - Port (default 23)
   - Username
   - Password
   - Enable password (optional)
3. Click **+ Add Device**.

The device is saved to `devices.json` and monitoring starts immediately.

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

Restart the script after manual edits.

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

## Telnet or SSH

Both are supported. Pick the protocol in the **Add New Device** form, or let the port decide:

| Port | Protocol used |
|------|---------------|
| 23 | Telnet |
| 22 | SSH (needs `pip install paramiko`) |

If Tera Term connects to a device on **port 22**, that device is SSH-only — choose **SSH** here too. Selecting Telnet against port 22 will sit in *Reconnecting…* forever, because the device never sends a `Username:` prompt.

In `devices.json` the protocol is stored per device:

```json
{
  "name": "Core-Router",
  "host": "10.136.110.254",
  "port": 22,
  "transport": "ssh",
  "username": "admin",
  "password": "yourpass",
  "enable_password": ""
}
```

Older IOS images sometimes only offer legacy SSH key exchange and ciphers; those legacy algorithms are enabled automatically.

---

## Limitations & Notes

- Supports **Telnet and SSH**. SSH requires Paramiko (`pip install paramiko`).
- Requires that the Cisco device still generates the syslog messages to the monitor session (normal default behavior).
- Device `exec-timeout` should not be extremely short; the keepalive interval is 50 seconds by default.
- This tool does **not** modify any configuration on the Cisco devices.
- For production use, change the default login credentials and `secret_key`.

---

## Troubleshooting

| Problem | Possible cause / solution |
|---------|---------------------------|
| `ModuleNotFoundError: No module named 'telnetlib'` | Python 3.13+ removed `telnetlib`. Make sure `telnet_client.py` sits in the same folder as `cisco_multi_monitor.py` (it is used automatically) |
| Stuck on *Reconnecting…* but Tera Term logs in fine | Tera Term is probably using **SSH (port 22)**. Set the device protocol to **SSH** |
| `SSH requires Paramiko` | Run `pip install paramiko` |
| SSH fails with "no matching key exchange/cipher" | Very old IOS image; legacy algorithms are already enabled, so upgrade the IOS SSH config or use Telnet for that device |
| Cannot connect | Check IP, port, username, password, and that Telnet/SSH is allowed |
| Session drops often | Lower `KEEPALIVE_INTERVAL` or check device `exec-timeout` |
| No email received | Check SMTP settings and Gmail App Password |
| No events appear | Confirm the device is generating the expected syslog messages (`terminal monitor` works when you login manually) |
| Sound does not play | Browser may block autoplay – click the Sound button once |

---

## License

Free to use and modify for personal or internal network operations use.
