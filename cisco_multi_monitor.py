#!/usr/bin/env python3
"""
Cisco Multi-Device Monitor
--------------------------
Lightweight tool that keeps persistent Telnet sessions to Cisco devices,
enables 'terminal monitor', detects interface / BGP / OSPF down events,
sends email alerts, and provides a web dashboard.

Features:
- Import device inventory from CSV / JSON (IP + credentials)
- Search inventory, then Connect / Stop monitoring on demand
- Add / remove devices from the web UI (saved to devices.json)
- Email notifications
- Browser sound notification (toggleable)
- Filter events by device
- Export events to CSV
- Simple login protection
- Auto-reconnect + keepalive

Author: Generated for network operations use
"""

try:
    from telnetlib import Telnet          # Python <= 3.12
except ModuleNotFoundError:               # telnetlib removed in Python 3.13
    from telnet_client import Telnet
from ssh_client import open_ssh_session
import time
import re
import smtplib
import threading
import json
import os
import csv
import io
from email.mime.text import MIMEText
from datetime import datetime
from collections import deque
from functools import wraps
from flask import (
    Flask, render_template_string, jsonify, request,
    Response, session, redirect, url_for
)

# ====================== CONFIG ======================
DEVICES_FILE = "devices.json"

# Dashboard login (CHANGE THESE!)
DASHBOARD_USER = "admin"
DASHBOARD_PASS = "admin123"

# Email settings
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = "your_email@gmail.com"
EMAIL_PASS = "your_app_password"          # Gmail: use App Password
EMAIL_TO   = "alert@yourdomain.com"

# Syslog patterns to detect
PATTERNS = [
    r"%LINEPROTO-5-UPDOWN.*changed state to down",
    r"%LINK-3-UPDOWN.*changed state to down",
    r"%BGP-5-ADJCHANGE.*Down",
    r"%BGP_SESSION-5-ADJCHANGE.*removed from session",
    r"%OSPF-5-ADJCHG.*from FULL to DOWN",
    r"%OSPF-5-ADJCHG.*Neighbor Down",
]

KEEPALIVE_INTERVAL = 50       # seconds
RECONNECT_DELAY = 12          # seconds
DASHBOARD_PORT = 5000
# ====================================================

# Shared state
events = deque(maxlen=500)
# Inventory list (source of truth in devices.json)
inventory_lock = threading.Lock()
# Live monitor state keyed by device name
device_status = {}
device_threads = {}
stop_flags = {}
status_lock = threading.Lock()

app = Flask(__name__)
app.secret_key = "change-this-to-a-long-random-string-please-32chars-min"

# -------------------- Auth --------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# -------------------- Device persistence --------------------
def load_devices():
    if os.path.exists(DEVICES_FILE):
        with open(DEVICES_FILE, "r") as f:
            return json.load(f)
    return []

def save_devices(devices):
    with open(DEVICES_FILE, "w") as f:
        json.dump(devices, f, indent=2)

def normalize_device(raw: dict) -> dict:
    """Normalize a device dict from UI / CSV / JSON import."""
    name = str(raw.get("name") or raw.get("hostname") or "").strip()
    host = str(raw.get("host") or raw.get("ip") or raw.get("address") or "").strip()
    if not name and host:
        name = host
    if not host:
        raise ValueError("host/ip required")
    if not name:
        raise ValueError("name required")

    transport = str(raw.get("transport") or raw.get("protocol") or "").strip().lower()
    if transport not in ("ssh", "telnet"):
        transport = ""

    raw_port = raw.get("port", "")
    try:
        port = int(raw_port) if str(raw_port).strip() else (22 if transport == "ssh" else 23)
    except (TypeError, ValueError):
        port = 22 if transport == "ssh" else 23

    if not transport:
        transport = "ssh" if port == 22 else "telnet"

    username = str(raw.get("username") or raw.get("user") or "").strip()
    password = str(raw.get("password") or raw.get("pass") or "")
    enable_password = str(
        raw.get("enable_password") or raw.get("enable") or raw.get("enable_pass") or ""
    )

    if not username or not password:
        raise ValueError(f"username/password required for {name}")

    return {
        "name": name,
        "host": host,
        "port": port,
        "transport": transport,
        "username": username,
        "password": password,
        "enable_password": enable_password,
    }

def resolve_transport(dev: dict) -> str:
    """Return 'ssh' or 'telnet'. Port 22 implies SSH unless stated otherwise."""
    transport = str(dev.get("transport") or dev.get("protocol") or "").strip().lower()
    if transport in ("ssh", "telnet"):
        return transport
    return "ssh" if int(dev.get("port", 23)) == 22 else "telnet"

def parse_devices_csv(text: str):
    """Parse CSV inventory. Flexible header names."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    # Normalize headers
    field_map = {}
    for h in reader.fieldnames:
        key = (h or "").strip().lower().replace(" ", "_")
        field_map[h] = key

    devices = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        mapped = {field_map[k]: (v or "").strip() for k, v in row.items() if k is not None}
        devices.append(normalize_device(mapped))
    return devices

def parse_devices_json(text: str):
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("devices") or data.get("networklist") or data.get("hosts") or []
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of devices or {devices:[...]}")
    return [normalize_device(item) for item in data]

def upsert_devices(incoming: list):
    """Merge imported devices into inventory by name (overwrite credentials/host)."""
    with inventory_lock:
        devices = load_devices()
        by_name = {d["name"]: i for i, d in enumerate(devices)}
        added, updated = 0, 0
        for dev in incoming:
            if dev["name"] in by_name:
                devices[by_name[dev["name"]]] = dev
                updated += 1
            else:
                by_name[dev["name"]] = len(devices)
                devices.append(dev)
                added += 1
        save_devices(devices)
        return {"ok": True, "added": added, "updated": updated, "total": len(devices)}

def find_device(name: str):
    with inventory_lock:
        for d in load_devices():
            if d["name"] == name:
                return d
    return None

def inventory_public():
    """Inventory without passwords for the UI."""
    with inventory_lock:
        devices = load_devices()
    out = []
    for d in devices:
        with status_lock:
            st = device_status.get(d["name"], {})
            monitoring = d["name"] in stop_flags and not stop_flags[d["name"]].is_set()
        out.append({
            "name": d["name"],
            "host": d["host"],
            "port": d.get("port", 23),
            "transport": resolve_transport(d),
            "username": d.get("username", ""),
            "monitoring": monitoring,
            "connected": bool(st.get("connected")),
            "last_keepalive": st.get("last_keepalive", "-"),
            "last_event": st.get("last_event", "-"),
            "last_error": st.get("last_error", ""),
        })
    return out

# -------------------- Login Page --------------------
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login - Cisco Monitor</title>
<style>
    body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
           display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
    .box { background: #1e293b; padding: 40px; border-radius: 12px; width: 100%; max-width: 360px;
           border: 1px solid #334155; }
    h2 { margin: 0 0 25px; text-align: center; }
    input { width: 100%; padding: 12px; margin-bottom: 15px; border-radius: 8px;
            border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 1rem; }
    button { width: 100%; padding: 12px; background: #3b82f6; color: white; border: none;
             border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 500; }
    button:hover { opacity: 0.9; }
    .error { color: #ef4444; text-align: center; margin-bottom: 15px; font-size: 0.9rem; }
</style>
</head>
<body>
<div class="box">
    <h2>Cisco Monitor Login</h2>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post">
        <input type="text" name="username" placeholder="Username" required autofocus>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Login</button>
    </form>
</div>
</body>
</html>
"""

# -------------------- Main Dashboard --------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cisco Multi-Device Monitor</title>
<style>
    :root {
        --bg: #0f172a; --card: #1e293b; --border: #334155;
        --text: #e2e8f0; --muted: #94a3b8;
        --green: #22c55e; --red: #ef4444; --blue: #3b82f6; --amber: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }
    .container { max-width: 1200px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; flex-wrap: wrap; gap: 12px; }
    h1 { font-size: 1.6rem; font-weight: 600; }
    .header-right { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .badge { background: var(--card); border: 1px solid var(--border); padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; color: var(--muted); }
    .btn-sm { background: var(--card); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 8px; font-size: 0.85rem; cursor: pointer; }
    .btn-sm:hover { background: #334155; }
    .btn-sm.active { background: var(--blue); border-color: var(--blue); color: white; }
    .btn-sm.danger { color: #f87171; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-bottom: 22px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; position: relative; }
    .card h3 { font-size: 1.05rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; padding-right: 70px; }
    .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
    .status-dot.up { background: var(--green); box-shadow: 0 0 8px var(--green); }
    .status-dot.down { background: var(--red); box-shadow: 0 0 8px var(--red); }
    .status-dot.idle { background: #64748b; }
    .meta { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
    .meta span { color: var(--text); }
    .card-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
    .btn-remove { position: absolute; top: 10px; right: 10px; background: transparent; border: 1px solid var(--border); color: var(--muted); border-radius: 6px; padding: 2px 8px; font-size: 0.72rem; cursor: pointer; }
    .btn-remove:hover { background: var(--red); color: white; border-color: var(--red); }
    .form-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 22px; }
    .form-card h3 { margin-bottom: 12px; font-size: 1.05rem; }
    .form-card p.hint { color: var(--muted); font-size: 0.82rem; margin: -6px 0 12px; }
    .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
    .toolbar input[type="search"] { flex: 1; min-width: 200px; }
    input, textarea { background: #0f172a; border: 1px solid var(--border); color: var(--text); padding: 9px 11px; border-radius: 8px; font-size: 0.9rem; width: 100%; }
    input:focus, textarea:focus { outline: none; border-color: var(--blue); }
    .btn { background: var(--blue); color: white; border: none; padding: 10px 18px; border-radius: 8px; font-size: 0.9rem; cursor: pointer; font-weight: 500; }
    .btn:hover { opacity: 0.9; }
    .btn.secondary { background: #334155; }
    .btn.success { background: #15803d; }
    .btn.warn { background: #b45309; }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .events-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
    .events-header { padding: 12px 18px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .filter-select { background: #0f172a; border: 1px solid var(--border); color: var(--text); padding: 6px 10px; border-radius: 6px; font-size: 0.85rem; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 10px 16px; background: #0f172a; color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.4px; }
    td { padding: 10px 16px; border-top: 1px solid var(--border); font-size: 0.86rem; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .time { color: var(--muted); white-space: nowrap; width: 145px; }
    .device-tag { display: inline-block; background: #1e3a5f; color: #93c5fd; padding: 2px 7px; border-radius: 4px; font-size: 0.7rem; }
    .empty { text-align: center; padding: 40px; color: var(--muted); }
    .import-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .import-row input[type="file"] { max-width: 280px; }
    .msg { font-size: 0.85rem; color: var(--muted); margin-top: 8px; }
    .msg.ok { color: var(--green); }
    .msg.err { color: var(--red); }
    .err-line { line-height: 1.35; word-break: break-word; }
    .count-badge { color: var(--muted); font-size: 0.85rem; }
    @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } .form-row { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Cisco Multi-Device Monitor</h1>
        <div class="header-right">
            <button class="btn-sm" id="sound-btn" onclick="toggleSound()">Sound: ON</button>
            <a href="/export" class="btn-sm" style="text-decoration:none;">Export CSV</a>
            <div class="badge" id="last-update">Loading...</div>
            <a href="/logout" class="btn-sm danger" style="text-decoration:none;">Logout</a>
        </div>
    </header>

    <div class="form-card">
        <h3>Import Network List</h3>
        <p class="hint">Upload CSV or JSON with IP + username/password. Devices are stored in inventory — connect only when you need to monitor syslog.</p>
        <div class="import-row">
            <input type="file" id="import-file" accept=".csv,.json,text/csv,application/json">
            <button class="btn secondary" onclick="importFile()">Import File</button>
            <a href="/sample/networklist.csv" class="btn-sm" style="text-decoration:none;">Sample CSV</a>
        </div>
        <div class="msg" id="import-msg"></div>
    </div>

    <div class="form-card">
        <h3>Device Inventory</h3>
        <div class="toolbar">
            <input type="search" id="search-q" placeholder="Search by name, IP, or username..." oninput="applySearch()">
            <span class="count-badge" id="inv-count">0 devices</span>
        </div>
        <div class="grid" id="devices"></div>
        <div class="empty" id="devices-empty" style="display:none;">No devices yet — import a network list or add one below.</div>
    </div>

    <div class="form-card">
        <h3>Add Single Device</h3>
        <div class="form-row">
            <input id="f-name" placeholder="Name (e.g. Core-Router)" required>
            <input id="f-host" placeholder="IP / Hostname" required>
            <select id="f-transport" class="filter-select" onchange="syncPort()">
                <option value="telnet">Telnet</option>
                <option value="ssh">SSH</option>
            </select>
            <input id="f-port" placeholder="Port" value="23">
            <input id="f-user" placeholder="Username" required>
            <input id="f-pass" type="password" placeholder="Password" required>
            <input id="f-enable" type="password" placeholder="Enable password (optional)">
        </div>
        <button class="btn" onclick="addDevice()">+ Add Device</button>
    </div>

    <div class="events-card">
        <div class="events-header">
            <span>Recent Events</span>
            <div style="display:flex;gap:10px;align-items:center;">
                <select class="filter-select" id="filter-device" onchange="applyFilter()">
                    <option value="all">All devices</option>
                </select>
                <span style="font-size:0.85rem;color:var(--muted)" id="event-count">0 events</span>
            </div>
        </div>
        <table>
            <thead>
                <tr><th>Time</th><th>Device</th><th>Message</th></tr>
            </thead>
            <tbody id="events-body">
                <tr><td colspan="3" class="empty">No events yet</td></tr>
            </tbody>
        </table>
    </div>
</div>

<script>
let lastEventCount = 0;
let allEvents = [];
let allInventory = [];
let soundEnabled = localStorage.getItem('soundEnabled') !== 'false';

const audio = new Audio("data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdH2Onp+gnZ2dm5qZmJeXl5aVlZSTk5KRkZCPj46NjYyLi4qJiYiHh4aFhYSDgoGAf39+fX18e3t6eXl4d3d2dXV0c3NycnFwcG9vbm1tbGxra2pqaWloaGdnZmVlZGRjY2JiYWFgX19eXl1dXFxbW1paWVlYWFdXVlZVVVRUU1NSUlFRUFBPTk5NTUxLS0pKSUlISEdHRkZFRURDQ0JCQUFAPz8+Pj09PDw7Ozo6OTk4ODc3NjY1NTQ0MzMyMjExMDAvLy4uLS0sLCsrKioqKSkpKCgoJycnJiYmJSUlJCQkIyMjIiIiISEhICAgHx8fHh4eHR0dHBwcGxsbGhoaGRkZGBgYFxcXFhYWFRUVFBQUExMTEhISERER");

function updateSoundBtn() {
    const btn = document.getElementById('sound-btn');
    btn.textContent = soundEnabled ? 'Sound: ON' : 'Sound: OFF';
    btn.classList.toggle('active', soundEnabled);
}
updateSoundBtn();

function toggleSound() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('soundEnabled', soundEnabled);
    updateSoundBtn();
}

function playSound() {
    if (!soundEnabled) return;
    audio.currentTime = 0;
    audio.play().catch(() => {});
}

function statusLabel(d) {
    if (d.connected) return { text: 'Connected', color: 'var(--green)', dot: 'up' };
    if (d.monitoring) return { text: 'Reconnecting…', color: 'var(--amber)', dot: 'down' };
    return { text: 'Idle', color: 'var(--muted)', dot: 'idle' };
}

function renderInventory(list) {
    const devicesEl = document.getElementById('devices');
    const emptyEl = document.getElementById('devices-empty');
    const filterSelect = document.getElementById('filter-device');
    const currentFilter = filterSelect.value;
    devicesEl.innerHTML = '';

    document.getElementById('inv-count').textContent = list.length + ' shown / ' + allInventory.length + ' total';

    if (list.length === 0) {
        emptyEl.style.display = 'block';
        emptyEl.textContent = allInventory.length === 0
            ? 'No devices yet — import a network list or add one below.'
            : 'No devices match your search.';
    } else {
        emptyEl.style.display = 'none';
    }

    // Rebuild filter options from full inventory
    const keep = new Set(['all']);
    filterSelect.innerHTML = '<option value="all">All devices</option>';
    allInventory.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.name;
        opt.textContent = d.name;
        filterSelect.appendChild(opt);
        keep.add(d.name);
    });
    if (keep.has(currentFilter)) filterSelect.value = currentFilter;

    list.forEach(d => {
        const st = statusLabel(d);
        const div = document.createElement('div');
        div.className = 'card';
        const monBtn = d.monitoring
            ? `<button class="btn btn-sm warn" onclick="stopMonitor('${escapeAttr(d.name)}')">Stop</button>`
            : `<button class="btn btn-sm success" onclick="startMonitor('${escapeAttr(d.name)}')">Connect & Monitor</button>`;
        const errorLine = d.last_error
            ? `<div class="meta err-line">Error: <span style="color:var(--red)">${escapeHtml(d.last_error)}</span></div>`
            : '';
        div.innerHTML = `
            <button class="btn-remove" onclick="removeDevice('${escapeAttr(d.name)}')">Remove</button>
            <h3><span class="status-dot ${st.dot}"></span>${escapeHtml(d.name)}</h3>
            <div class="meta">Host: <span>${escapeHtml(d.host)}:${d.port}</span></div>
            <div class="meta">Protocol: <span>${(d.transport || 'telnet').toUpperCase()}</span></div>
            <div class="meta">User: <span>${escapeHtml(d.username)}</span></div>
            <div class="meta">Status: <span style="color:${st.color}">${st.text}</span></div>
            <div class="meta">Last keepalive: <span>${escapeHtml(d.last_keepalive)}</span></div>
            <div class="meta">Last event: <span>${escapeHtml(d.last_event)}</span></div>
            ${errorLine}
            <div class="card-actions">${monBtn}</div>
        `;
        devicesEl.appendChild(div);
    });
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function escapeAttr(s) {
    return String(s).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
}

function applySearch() {
    const q = (document.getElementById('search-q').value || '').trim().toLowerCase();
    if (!q) {
        renderInventory(allInventory);
        return;
    }
    const filtered = allInventory.filter(d =>
        d.name.toLowerCase().includes(q) ||
        d.host.toLowerCase().includes(q) ||
        (d.username || '').toLowerCase().includes(q)
    );
    renderInventory(filtered);
}

function render(data) {
    allInventory = data.inventory || [];
    applySearch();

    allEvents = data.events || [];
    applyFilter();

    if (allEvents.length > lastEventCount && lastEventCount > 0) {
        playSound();
    }
    lastEventCount = allEvents.length;

    document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

function applyFilter() {
    const filter = document.getElementById('filter-device').value;
    const tbody = document.getElementById('events-body');
    const count = document.getElementById('event-count');
    let filtered = filter === "all" ? allEvents : allEvents.filter(e => e.device === filter);

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty">No events yet</td></tr>';
        count.textContent = '0 events';
    } else {
        tbody.innerHTML = '';
        filtered.forEach(e => {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td class="time">${escapeHtml(e.time)}</td><td><span class="device-tag">${escapeHtml(e.device)}</span></td><td>${escapeHtml(e.msg)}</td>`;
            tbody.appendChild(tr);
        });
        count.textContent = filtered.length + ' events';
    }
}

async function refresh() {
    try {
        const r = await fetch('/api/status');
        const data = await r.json();
        render(data);
    } catch (e) { console.error(e); }
}

async function importFile() {
    const fileInput = document.getElementById('import-file');
    const msg = document.getElementById('import-msg');
    if (!fileInput.files || !fileInput.files[0]) {
        msg.className = 'msg err';
        msg.textContent = 'Choose a CSV or JSON file first.';
        return;
    }
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    msg.className = 'msg';
    msg.textContent = 'Importing…';
    try {
        const r = await fetch('/api/devices/import', { method: 'POST', body: fd });
        const res = await r.json();
        if (res.ok) {
            msg.className = 'msg ok';
            msg.textContent = `Imported: ${res.added} added, ${res.updated} updated (${res.total} total). Search and click Connect & Monitor when needed.`;
            fileInput.value = '';
            refresh();
        } else {
            msg.className = 'msg err';
            msg.textContent = res.error || 'Import failed';
        }
    } catch (e) {
        msg.className = 'msg err';
        msg.textContent = String(e);
    }
}

function syncPort() {
    const transport = document.getElementById('f-transport').value;
    document.getElementById('f-port').value = transport === 'ssh' ? '22' : '23';
}

async function addDevice() {
    const transport = document.getElementById('f-transport').value;
    const payload = {
        name: document.getElementById('f-name').value.trim(),
        host: document.getElementById('f-host').value.trim(),
        transport: transport,
        port: parseInt(document.getElementById('f-port').value) || (transport === 'ssh' ? 22 : 23),
        username: document.getElementById('f-user').value.trim(),
        password: document.getElementById('f-pass').value,
        enable_password: document.getElementById('f-enable').value
    };
    if (!payload.name || !payload.host || !payload.username || !payload.password) {
        alert("Please fill Name, Host, Username and Password");
        return;
    }
    const r = await fetch('/api/devices', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    const res = await r.json();
    if (res.ok) {
        ['f-name','f-host','f-user','f-pass','f-enable'].forEach(id => document.getElementById(id).value = '');
        syncPort();
        refresh();
    } else {
        alert(res.error || "Failed to add device");
    }
}

async function startMonitor(name) {
    const r = await fetch('/api/devices/' + encodeURIComponent(name) + '/monitor', { method: 'POST' });
    const res = await r.json();
    if (!res.ok) alert(res.error || 'Failed to start monitor');
    refresh();
}

async function stopMonitor(name) {
    const r = await fetch('/api/devices/' + encodeURIComponent(name) + '/stop', { method: 'POST' });
    const res = await r.json();
    if (!res.ok) alert(res.error || 'Failed to stop');
    refresh();
}

async function removeDevice(name) {
    if (!confirm(`Remove device "${name}" from inventory?`)) return;
    const r = await fetch('/api/devices/' + encodeURIComponent(name), { method: 'DELETE' });
    const res = await r.json();
    if (res.ok) refresh();
    else alert(res.error || "Failed to remove");
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""

SAMPLE_CSV = """name,host,transport,port,username,password,enable_password
Core-Router,192.168.1.1,telnet,23,admin,cisco123,
Edge-Switch,192.168.1.2,telnet,23,admin,cisco123,enablepass
SSH-Router,10.136.110.254,ssh,22,admin,secret,
"""

# -------------------- Routes --------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == DASHBOARD_USER and
            request.form.get("password") == DASHBOARD_PASS):
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Invalid username or password"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/sample/networklist.csv")
@login_required
def sample_csv():
    return Response(
        SAMPLE_CSV,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=networklist_sample.csv"}
    )

@app.route("/api/status")
@login_required
def api_status():
    return jsonify({
        "inventory": inventory_public(),
        "events": list(events)[:120]
    })

@app.route("/api/devices", methods=["GET"])
@login_required
def api_list_devices():
    q = (request.args.get("q") or "").strip().lower()
    items = inventory_public()
    if q:
        items = [
            d for d in items
            if q in d["name"].lower() or q in d["host"].lower() or q in (d.get("username") or "").lower()
        ]
    return jsonify({"ok": True, "devices": items})

@app.route("/api/devices/import", methods=["POST"])
@login_required
def api_import_devices():
    try:
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            raw = f.read().decode("utf-8-sig", errors="replace")
            name = (f.filename or "").lower()
            if name.endswith(".json"):
                devices = parse_devices_json(raw)
            else:
                devices = parse_devices_csv(raw)
        elif request.is_json:
            body = request.get_json(force=True)
            if isinstance(body, list):
                devices = [normalize_device(x) for x in body]
            elif isinstance(body, dict) and "devices" in body:
                devices = [normalize_device(x) for x in body["devices"]]
            elif isinstance(body, dict) and "csv" in body:
                devices = parse_devices_csv(body["csv"])
            else:
                return jsonify({"ok": False, "error": "Send file upload, JSON list, or {devices:[...]}"})
        else:
            return jsonify({"ok": False, "error": "No file or JSON body provided"})

        if not devices:
            return jsonify({"ok": False, "error": "No devices found in import"})
        return jsonify(upsert_devices(devices))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/devices", methods=["POST"])
@login_required
def api_add_device():
    try:
        data = request.json or {}
        new_dev = normalize_device(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    with inventory_lock:
        devices = load_devices()
        if any(d["name"] == new_dev["name"] for d in devices):
            return jsonify({"ok": False, "error": "Device name already exists"})
        devices.append(new_dev)
        save_devices(devices)

    # Inventory only — do not auto-connect (user clicks Connect & Monitor)
    return jsonify({"ok": True})

@app.route("/api/devices/<name>/monitor", methods=["POST"])
@login_required
def api_start_monitor(name):
    dev = find_device(name)
    if not dev:
        return jsonify({"ok": False, "error": "Device not found in inventory"})
    if name in stop_flags and not stop_flags[name].is_set():
        return jsonify({"ok": True, "message": "Already monitoring"})
    start_device_monitor(dev)
    return jsonify({"ok": True})

@app.route("/api/devices/<name>/stop", methods=["POST"])
@login_required
def api_stop_monitor(name):
    if name in stop_flags:
        stop_flags[name].set()
    with status_lock:
        if name in device_status:
            device_status[name]["connected"] = False
            device_status[name]["last_keepalive"] = "-"
    device_threads.pop(name, None)
    # Keep last status entry for UI until removed from inventory
    return jsonify({"ok": True})

@app.route("/api/devices/<name>", methods=["DELETE"])
@login_required
def api_remove_device(name):
    with inventory_lock:
        devices = load_devices()
        devices = [d for d in devices if d["name"] != name]
        save_devices(devices)

    if name in stop_flags:
        stop_flags[name].set()
    with status_lock:
        device_status.pop(name, None)
    device_threads.pop(name, None)
    stop_flags.pop(name, None)
    return jsonify({"ok": True})

@app.route("/export")
@login_required
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Time", "Device", "Message"])
    for e in list(events):
        writer.writerow([e["time"], e["device"], e["msg"]])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=cisco_events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

# -------------------- Email --------------------
def send_email(subject: str, body: str):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        print(f"[EMAIL] {subject}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

def add_event(device_name: str, msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    events.appendleft({"time": now, "device": device_name, "msg": msg})
    with status_lock:
        if device_name in device_status:
            device_status[device_name]["last_event"] = now
    send_email(f"[Cisco Alert] {device_name}", f"Device : {device_name}\nTime   : {now}\n\n{msg}")

# -------------------- Connect --------------------
def describe_error(exc: Exception, transport: str, port: int = 0) -> str:
    """Turn a connection exception into a short hint shown on the device card."""
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()

    if transport == "telnet" and int(port or 0) == 22:
        return "Port 22 is SSH - change this device's protocol to SSH"
    if "authentication" in lowered or "auth failed" in lowered:
        return "Authentication failed - check username / password"
    if "not a valid rsa" in lowered or "no matching" in lowered:
        return f"SSH algorithm mismatch: {text}"
    if "banner" in lowered:
        return "No SSH banner - wrong port, firewall, or SSH not enabled"
    if "timed out" in lowered or isinstance(exc, TimeoutError):
        if transport == "telnet":
            return "Timed out - device may be SSH-only (try protocol SSH)"
        return "Timed out - host unreachable or SSH blocked"
    if "refused" in lowered:
        return f"Connection refused on this port - is {transport.upper()} enabled?"
    if "unreachable" in lowered or "no route" in lowered:
        return "Network unreachable from this PC"
    if "paramiko" in lowered:
        return "SSH needs Paramiko: pip install paramiko"
    return text[:160]

def open_telnet_session(dev: dict):
    tn = Telnet(dev["host"], dev["port"], timeout=12)

    tn.read_until(b"Username:", timeout=8)
    tn.write(dev["username"].encode() + b"\n")
    tn.read_until(b"Password:", timeout=8)
    tn.write(dev["password"].encode() + b"\n")

    idx, _, _ = tn.expect([b">", b"#"], timeout=8)
    if idx == -1:
        raise RuntimeError(
            "No Cisco prompt received. If this device uses SSH (port 22), "
            "set its protocol to SSH."
        )
    if idx == 0:
        tn.write(b"enable\n")
        if dev.get("enable_password"):
            tn.read_until(b"Password:", timeout=5)
            tn.write(dev["enable_password"].encode() + b"\n")
        tn.expect([b"#"], timeout=8)

    tn.write(b"terminal length 0\n")
    tn.write(b"terminal monitor\n")
    tn.write(b"\n")
    time.sleep(0.3)
    tn.read_very_eager()
    return tn

def open_session(dev: dict):
    """Open a Telnet or SSH monitoring session depending on the device."""
    if resolve_transport(dev) == "ssh":
        return open_ssh_session(dev)
    return open_telnet_session(dev)

# -------------------- Monitor --------------------
def monitor_device(dev: dict, stop_event: threading.Event):
    name = dev["name"]
    transport = resolve_transport(dev)
    tn = None
    last_keepalive = 0

    with status_lock:
        device_status[name] = {
            "connected": False,
            "last_keepalive": "-",
            "last_event": device_status.get(name, {}).get("last_event", "-"),
            "host": dev["host"],
            "transport": transport,
            "last_error": ""
        }

    while not stop_event.is_set():
        try:
            if tn is None:
                print(f"[{name}] Connecting to {dev['host']}:{dev['port']} over {transport.upper()}...")
                tn = open_session(dev)

                with status_lock:
                    device_status[name]["connected"] = True
                    device_status[name]["last_error"] = ""
                print(f"[{name}] Connected + terminal monitor ON ({transport.upper()})")

            data = tn.read_very_eager().decode(errors="ignore")
            if data:
                for line in data.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    print(f"[{name}] {line}")
                    for pat in PATTERNS:
                        if re.search(pat, line, re.IGNORECASE):
                            add_event(name, line)
                            break

            if time.time() - last_keepalive > KEEPALIVE_INTERVAL:
                tn.write(b"\n")
                last_keepalive = time.time()
                with status_lock:
                    device_status[name]["last_keepalive"] = datetime.now().strftime("%H:%M:%S")

            time.sleep(0.35)

        except Exception as e:
            reason = describe_error(e, transport, dev.get("port", 0))
            print(f"[{name}] Lost: {e}")
            with status_lock:
                if name in device_status:
                    device_status[name]["connected"] = False
                    device_status[name]["last_error"] = reason
            try:
                if tn:
                    tn.close()
            except Exception:
                pass
            tn = None
            for _ in range(RECONNECT_DELAY * 2):
                if stop_event.is_set():
                    break
                time.sleep(0.5)

    try:
        if tn:
            tn.close()
    except Exception:
        pass
    with status_lock:
        if name in device_status:
            device_status[name]["connected"] = False
    print(f"[{name}] Monitor stopped")

def start_device_monitor(dev: dict):
    name = dev["name"]
    # Stop previous thread if any
    if name in stop_flags and not stop_flags[name].is_set():
        stop_flags[name].set()
        time.sleep(0.2)
    stop_event = threading.Event()
    stop_flags[name] = stop_event
    t = threading.Thread(target=monitor_device, args=(dev, stop_event), daemon=True)
    device_threads[name] = t
    t.start()

# -------------------- Main --------------------
if __name__ == "__main__":
    # Inventory loads from devices.json; monitoring starts only when user clicks Connect
    n = len(load_devices())
    print(f"\nDashboard → http://0.0.0.0:{DASHBOARD_PORT}")
    print(f"Login     → user: {DASHBOARD_USER}  /  pass: {DASHBOARD_PASS}")
    print(f"Inventory → {n} device(s) loaded (idle until Connect & Monitor)")
    print("Press Ctrl+C to stop\n")
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)
