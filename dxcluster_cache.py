#!/usr/bin/env python3
"""
DXCluster Cache — DX Cluster Spot Cache with Web Portal

Author: Bruno, CS8ABG
License: MIT License

Description:
    - Connects to a DX Cluster node via Telnet
    - Parses spot lines
    - Caches recent spots in memory (configurable size)
    - Performs DXCC lookups via HTTP API with caching
    - Exposes RESTful JSON API and web-based portal for administration
    - Supports sending new DX spots to the cluster

Usage:
    python3 dxcluster_cache.py

MIT License:
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

import threading
import time
import telnetlib3 as telnetlib
import re
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, redirect, url_for, render_template_string, Response
import requests
from collections import deque
import logging
from functools import wraps

# -----------------------------
# Logging setup
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# -----------------------------
# Config and persistence
# -----------------------------
CONFIG_FILE = Path("./dxcluster_config.json")
CLUSTERS_FILE = Path("./clusters.txt")
DEFAULT_CONFIG = {
    "host": "dxc.example.org",
    "port": 7300,
    "call": "N0CALL",
    "maxcache": 500,
    "webport": 8000,
    "dxcc_lookup_url": "http://log.your.site/api/lookup",
    "dxcc_lookup_key": "api-key-goes-here",
    "portal_user": "",
    "portal_pass": ""
}

_lock = threading.RLock()


def load_config():
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r") as fh:
                cfg = json.load(fh)
                for k, v in DEFAULT_CONFIG.items():
                    cfg.setdefault(k, v)
                return cfg
        except Exception:
            return DEFAULT_CONFIG.copy()
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with CONFIG_FILE.open("w") as fh:
        json.dump(cfg, fh, indent=2)


config = load_config()

# -----------------------------
# Spot cache and helpers
# -----------------------------
spots = deque(maxlen=config.get("maxcache", 500))
consecutive_dxcc_errors = 0
dxcc_cache = {}
dxcc_cache_ttl = 3600  # 1 hour


def qrg2band_khz(qrg_khz):
    try:
        f = float(qrg_khz)
    except Exception:
        return ""
    if 1000 < f < 2000:
        return "160m"
    if 3000 < f < 4000:
        return "80m"
    if 6000 < f < 8000:
        return "40m"
    if 9000 < f < 11000:
        return "30m"
    if 13000 < f < 15000:
        return "20m"
    if 17000 < f < 19000:
        return "17m"
    if 20000 < f < 22000:
        return "15m"
    if 23000 < f < 25000:
        return "12m"
    if 27000 < f < 30000:
        return "10m"
    if 49000 < f < 52000:
        return "6m"
    if 69000 < f < 71000:
        return "4m"
    if 140000 < f < 150000:
        return "2m"
    if 430000 < f < 440000:
        return "70cm"
    return ""


def to_uc_word(s):
    return " ".join(x.capitalize() for x in (s or "").split())

def load_clusters():
    clusters = []
    if CLUSTERS_FILE.exists():
        with CLUSTERS_FILE.open("r") as f:
            for line in f:
                line = line.strip()
                if line and "," in line:
                    name, hp = line.split(",", 1)
                    clusters.append({"name": name.strip(), "hostport": hp.strip()})
    return clusters

def save_clusters(clusters):
    with CLUSTERS_FILE.open("w") as f:
        for c in clusters:
            f.write(f"{c['name']},{c['hostport']}\n")
            
def send_spot(frequency, callsign, remarks):
    global client
    if not client.connected or not client.tn:
        logging.warning("DXCluster client not connected; cannot send spot.")
        return False
    try:
        cmd = f"dx {frequency} {callsign} {remarks}\n"
        client.tn.write(cmd.encode())
        logging.info(f"Sent spot: {cmd.strip()}")
        return True
    except Exception as e:
        logging.error(f"Error sending spot: {e}")
        return False


# -----------------------------
# DXCC Lookup
# -----------------------------
def dxcc_lookup(call):
    global consecutive_dxcc_errors
    now = time.time()
    cached = dxcc_cache.get(call)
    if cached and (now - cached["t"]) < dxcc_cache_ttl:
        return cached["v"]

    cfg = load_config()
    url = cfg.get("dxcc_lookup_url")
    key = cfg.get("dxcc_lookup_key")
    payload = {"key": key, "callsign": call}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            consecutive_dxcc_errors += 1
            logging.warning(f"DXCC lookup HTTP {resp.status_code} for {call}")
            return None
        data = resp.json()
        # normalize fields with sane fallbacks
        result = {
            "cont": data.get("cont", ""),
            "entity": to_uc_word(data.get("dxcc", "")),
            "flag": data.get("dxcc_flag") or "",
            "dxcc_id": str(data.get("dxcc_id") or ""),
            "lotw_user": data.get("lotw_member") if data.get("lotw_member") is not None else False,
            "lat": data.get("dxcc_lat") if data.get("dxcc_lat") is not None else None,
            "lng": data.get("dxcc_long") or data.get("dxcc_lon") or None,
            "cqz": data.get("dxcc_cqz") or None,
        }
        dxcc_cache[call] = {"v": result, "t": now}
        consecutive_dxcc_errors = 0
        return result
    except Exception as e:
        consecutive_dxcc_errors += 1
        logging.error(f"DXCC lookup exception for {call}: {e}")
        return None


# -----------------------------
# Telnet client
# -----------------------------
TELNET_RE = re.compile(
    r"DX\s+de\s+(?P<spotter>\S+):\s+(?P<freq>[0-9]+\.?[0-9]*)\s+(?P<spotted>\S+)\s+(?P<rest>.+?)\s+(?P<time>[0-9]{3,4}Z)"
)

SHDX_RE = re.compile(
    r"^(?P<freq>[0-9]+\.?[0-9]*)\s+"          # frequency
    r"(?P<spotted>\S+)\s+"                     # spotted callsign
    r"(?P<date>[0-9]{2}-[A-Za-z]{3}-[0-9]{4})\s+"  # date
    r"(?P<time>[0-9]{4}Z)\s+"                  # time
    r"(via\s+(?P<via>\S+)\s+)?(?P<message>.+?)" # optional via + message
    r"\s*<(?P<spotter>\S+)>$"                  # spotter callsign in <>
)


class DXClusterClient(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.tn = None
        self.connected = False

    def stop(self):
        self._stop_event.set()
        try:
            if self.tn:
                self.tn.close()
        except Exception:
            pass

    def run(self):
        while not self._stop_event.is_set():
            cfg = load_config()
            host, port, call = cfg["host"], cfg["port"], cfg["call"]
            try:
                logging.info(f"Connecting to {host}:{port} ...")
                self.tn = telnetlib.Telnet(host, port, timeout=15)
                self.connected = True
                logging.info("Connected, waiting for login prompt...")
                time.sleep(1)

                prompt_buf = b""
                got_login = False
                start = time.time()
                PROMPT_RE = re.compile(r"(?i)\b(login|call|callsign)\s*:\s*$")

                while time.time() - start < 30 and not got_login:
                    try:
                        chunk = self.tn.read_very_eager()
                        if chunk:
                            prompt_buf += chunk
                            text = prompt_buf.decode(errors="ignore")
                            # split by lines to catch prompts appearing midstream
                            for line in text.splitlines():
                                if PROMPT_RE.search(line.strip()):
                                    got_login = True
                                    break
                        if got_login:
                            break
                        time.sleep(0.2)
                    except EOFError:
                        break

                if got_login:
                    logging.info(f"Login prompt detected — sending {call}")
                    self.tn.write(call.encode() + b"\n")
                    logging.info("Sending sh/dx command.")
                    self.tn.write(b"sh/dx/100\n")
                else:
                    logging.info("No login prompt found within timeout; proceeding anyway.")

                while not self._stop_event.is_set():
                    try:
                        line = self.tn.read_until(b"\n", timeout=5)
                        if not line:
                            continue
                        text = line.decode(errors="ignore").strip()
                        if not text:
                            continue

                        m = TELNET_RE.search(text)
                        if m:
                            spotter = m.group("spotter")
                            freq = m.group("freq")
                            spotted = m.group("spotted")
                            msg = m.group("rest").strip()
                            when = parse_z_time(m.group("time"))

                            spot = {
                                "spotter": spotter,
                                "spotted": spotted,
                                "frequency": str(int(float(freq))),
                                "message": msg,
                                "when": when.isoformat().replace("+00:00", "Z"),
                                "source": detect_source_from_message(msg),
                                "band": qrg2band_khz(freq),
                            }

                            threading.Thread(target=populate_dxcc, args=(spot,), daemon=True).start()
                            logging.info(
                                f"{spotter}->{spotted} {freq}kHz {spot['band']} {msg[:60]}{'...' if len(msg)>60 else ''}"
                            )

                            with _lock:
                                global spots
                                cfg_now = load_config()
                                desired_max = cfg_now.get("maxcache", 500)
                                if spots.maxlen != desired_max:
                                    spots = deque(list(spots), maxlen=desired_max)
                                spots.append(spot)
                        else:
                            m2 = SHDX_RE.match(text)
                            if m2:
                                freq = m2.group("freq")
                                spotted = m2.group("spotted")
                                date_str = m2.group("date")
                                timestr = m2.group("time")
                                via = m2.group("via")
                                message = m2.group("message").strip()
                                spotter = m2.group("spotter")

                                # combine date + time into datetime
                                now = datetime.now(timezone.utc)
                                dt = datetime.strptime(f"{date_str} {timestr}", "%d-%b-%Y %H%MZ").replace(
                                    tzinfo=timezone.utc,
                                    second=now.second,
                                    microsecond=now.microsecond
                                )
                                if via:
                                    message = f"{message} via {via}"

                                spot = {
                                    "spotter": spotter,
                                    "spotted": spotted,
                                    "frequency": int(float(freq)),
                                    "message": message,
                                    "when": dt.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                                    "source": "cluster",
                                    "band": qrg2band_khz(freq),
                                }

                                # populate DXCC asynchronously
                                threading.Thread(target=populate_dxcc, args=(spot,), daemon=True).start()
                                logging.info(
                                f"{spotter}->{spotted} {freq}kHz {spot['band']} {message[:60]}{'...' if len(message)>60 else ''}"
                                )
                                
                                with _lock:
                                    spots.append(spot)

                    except EOFError:
                        logging.warning("Telnet EOF — reconnecting soon")
                        break
                    except Exception as e:
                        logging.error(f"Telnet error: {e}")
                        time.sleep(1)
            except Exception as e:
                logging.error(f"Connection error: {e}")

            self.connected = False
            if self.tn:
                try:
                    self.tn.close()
                except Exception:
                    pass
            time.sleep(5)


def parse_z_time(timestr):
    now = datetime.now(timezone.utc)
    t = timestr.rstrip("Z")
    if len(t) in (3, 4):
        if len(t) == 3:
            hh, mm = int(t[0]), int(t[1:3])
        else:
            hh, mm = int(t[0:2]), int(t[2:4])
        # Include current seconds and microseconds
        when = datetime(
            now.year, now.month, now.day,
            hh, mm, now.second, now.microsecond,
            tzinfo=timezone.utc
        )
        # Adjust for previous day if necessary
        if when - now > timedelta(hours=12):
            when -= timedelta(days=1)
        return when
    return now


def detect_source_from_message(msg):
    m = msg.lower()
    if "pota" in m:
        return "pota"
    if "cq" in m:
        return "cq"
    return "cluster"


def populate_dxcc(spot):
    spotter, spotted = spot.get("spotter"), spot.get("spotted")
    s1, s2 = dxcc_lookup(spotter), dxcc_lookup(spotted)
    if s1:
        spot["dxcc_spotter"] = s1
    if s2:
        spot["dxcc_spotted"] = s2


# -----------------------------
# Web API + Portal
# -----------------------------
app = Flask(__name__)
client = DXClusterClient()


@app.route("/spots/")
def api_spots():
    with _lock:
        result = []
        for s in reversed(spots):  # newest first
            try:
                when_obj = datetime.fromisoformat(s.get("when").replace("Z", "+00:00"))
                when_str = when_obj.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            except Exception:
                when_str = s.get("when")

            result.append({
                "spotter": s.get("spotter"),
                "spotted": s.get("spotted"),
                "frequency": s.get("frequency"),
                "message": s.get("message", ""),
                "when": when_str,
                "source": s.get("source", "cluster"),
                "dxcc_spotter": s.get("dxcc_spotter", {}),
                "dxcc_spotted": s.get("dxcc_spotted", {}),
                "band": s.get("band", ""),
            })
        return app.response_class(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json"
        )


@app.route("/spot/<int:qrg_khz>")
def api_spot(qrg_khz):
    q = str(qrg_khz)
    chosen, youngest = None, None
    with _lock:
        for s in spots:
            if s.get("frequency") == q:
                t = datetime.fromisoformat(s.get("when").replace("Z", "+00:00"))
                if youngest is None or t > youngest:
                    youngest, chosen = t, s
    return jsonify(chosen or {})


@app.route("/spots/<band>")
def api_spots_band(band):
    with _lock:
        return jsonify([s for s in spots if s.get("band") == band])


@app.route("/stats")
def api_stats():
    return jsonify({"entries": len(spots), "connected": client.connected})

PORTAL_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>DXCluster Service Portal</title>
<style>
body { margin:0; font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background:#f4f4f8; color:#222; }
h1,h2,h3 { margin:0; }

/* Top bar with gear dropdown */
.topbar {
    background:#007bff;
    color:white;
    display:flex;
    align-items:center;
    justify-content:flex-end;
    padding:10px 20px;
    position:relative;
}
.topbar .gear {
    font-size:18px;
    cursor:pointer;
    user-select:none;
}
.topbar .gear:hover {
    font-weight: 600;
}
.topbar .info {
    font-size:18px;
    cursor:pointer;
    user-select:none;
}
.topbar .info:hover {
    font-weight: 600;
}
.dropdown {
    display:none;
    position:absolute;
    top:45px;
    right:20px;
    background:white;
    color:#222;
    box-shadow:0 3px 10px rgba(0,0,0,0.15);
    border-radius:6px;
    overflow:hidden;
    min-width:180px;
    z-index:2000;
}
.dropdown a {
    display:block;
    padding:10px 15px;
    text-decoration:none;
    color:#222;
    font-weight:500;
}
.dropdown a:hover { background:#f4f4f4; }

/* Panels */
.spots-panel {
    margin: 30px auto;
    background: white;
    border-radius: 10px;
    padding: 10px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    width: 90%;
    max-width: 1000px;
}

.spots-panel h2 {
    margin-bottom: 10px;
}

/* Terminal styles */
.terminal {
    background: #1e1e1e;
    color: #d4d4d4;
    font-family: "Courier New", Courier, monospace;
    font-size: 13px;
    padding: 10px;
    border-radius: 8px;
    max-height: 400px;
    overflow-y: auto;
    white-space: pre-wrap;
    line-height: 1.3em;
    box-shadow: inset 0 0 8px rgba(0,0,0,0.5);
}
.terminal-line {
    padding: 1px 0;
}
.terminal-line:nth-child(even) { background: rgba(255,255,255,0.02); }

/* Footer */
.footer { position:fixed; bottom:0; left:0; width:100%; background:#222; color:white; padding:10px 20px; display:flex; gap:25px; font-weight:600; align-items:center; }

/* Modal Styles */
.modal { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background: rgba(0,0,0,0.5); justify-content:center; align-items:center; z-index:1000; overflow:auto; }
.modal-content { background:#fff; padding:20px; border-radius:10px; width:500px; max-width:90%; max-height:80%; overflow-y:auto; box-shadow:0 3px 10px rgba(0,0,0,0.2); }
.modal-content h3 { margin-top:0; }
.modal-row { display:flex; gap:5px; margin-bottom:6px; align-items:center; }
.modal-row input, .modal-row select { flex:1; padding:5px; border-radius:4px; border:1px solid #ccc; }
.modal-buttons { text-align:right; margin-top:10px; }
.modal-buttons button { margin-left:5px; padding:5px 10px; border-radius:4px; border:none; cursor:pointer; font-weight:600; }
.modal-buttons .save { background:#28a745; color:white; }
.modal-buttons .cancel { background:#dc3545; color:white; }
.cluster-remove { background:#dc3545; color:white; border:none; border-radius:4px; padding:5px 10px; cursor:pointer; font-weight:600;}
.cluster-add { background:#007bff; color:white; border:none; border-radius:4px; padding:6px 12px; cursor:pointer; font-weight:600;}
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <div style="display:flex; align-items:center; gap:20px;">
    <div class="gear" onclick="toggleDropdown()">Settings ▼</div>
    <div class="info" onclick="openHelpModal()">API</div>
  </div>

  <div id="gearDropdown" class="dropdown">
    <a href="#" onclick="openSetupModal();closeDropdown();">Setup</a>
    <a href="#" onclick="openClusterModal();closeDropdown();">Edit Clusters</a>
    <a href="#" onclick="restartClient();closeDropdown();">Restart DXCluster Client</a>
    <a href="/logout" onclick="closeDropdown();">Logout</a>
  </div>
</div>

<!-- Recent Spots Terminal -->
<div class="spots-panel">
  <h2>Recent Spots</h2>
  <div class="terminal" id="spotsTerminal">
    {% for s in recent %}
      <div class="terminal-line">
        {{ s.when }} | {{ s.spotter }} -> {{ s.spotted }} | {{ s.frequency }}kHz | {{ s.band }} | {{ s.message|e|replace('\n',' ')|safe }}
      </div>
    {% endfor %}
  </div>
</div>
<!-- Send Spot Form -->
<div class="spots-panel">
  <h2>Send Spot</h2>
  <form method="post" action="/sndspot" onsubmit="return sendSpotJS(event)">
    <div class="modal-row">
      <label>Frequency (kHz):</label>
      <input name="frequency" id="spotFrequency" required>
    </div>
    <div class="modal-row">
      <label>Callsign:</label>
      <input name="callsign" id="spotCallsign" required>
    </div>
    <div class="modal-row">
      <label>Remarks:</label>
      <input name="remarks" id="spotRemarks">
    </div>
    <div class="modal-buttons">
      <button type="submit" class="save">Send Spot</button>
    </div>
  </form>
  <div id="spotStatus" style="margin-top:5px;"></div>
</div>

<!-- Footer -->
<div class="footer">
  <div id="connectionStatus">
    DXCluster connection:
    {% if connected %}
      <span style="color:#28a745;">Connected</span>
    {% else %}
      <span style="color:#dc3545;">Disconnected</span>
    {% endif %}
  </div>
  <div>Callsign: <strong>{{ cfg.call }}</strong></div>
  <div>Connected to: <strong>{{ cfg.host }}:{{ cfg.port }}</strong></div>
  <div id="cachedSpots">Cached spots: {{ entries }}</div>
</div>

<!-- Setup Modal -->
<div id="setupModal" class="modal">
  <div class="modal-content">
    <h3>Service Setup</h3>
    <form method="post" action="/config">
      <div class="modal-row">
        <label>Callsign:</label>
        <input name="call" value="{{cfg.call}}" required>
      </div>
      <div class="modal-row">
        <label>Cluster:</label>
        <select name="cluster">
          {% for c in clusters %}
            {% set hp = c.hostport %}
            <option value="{{ hp }}" {% if cfg.host + ':' + cfg.port|string == hp %}selected{% endif %}>
              {{ c.name }} ({{ hp }})
            </option>
          {% endfor %}
        </select>
      </div>
      <div class="modal-row">
        <label>Max Cache:</label>
        <input name="maxcache" type="number" value="{{cfg.maxcache}}" required>
      </div>
      <div class="modal-row">
        <label>Web Port:</label>
        <input name="webport" type="number" value="{{cfg.webport}}" required>
      </div>
      <div class="modal-row">
        <label>Lookup URL:</label>
        <input name="dxcc_lookup_url" value="{{cfg.dxcc_lookup_url}}" required>
      </div>
      <div class="modal-row">
        <label>Lookup Key:</label>
        <input name="dxcc_lookup_key" value="{{cfg.dxcc_lookup_key}}" required>
      </div>
      <div class="modal-buttons">
        <button type="submit" class="save">Save</button>
        <button type="button" class="cancel" onclick="closeSetupModal()">Cancel</button>
      </div>
    </form>
  </div>
</div>

<!-- Existing Cluster Edit Modal -->
<div id="clusterModal" class="modal">
  <div class="modal-content">
    <h3>Edit Clusters</h3>
    <form method="post" action="/clusters/save" id="clusterForm">
      <div id="clusterList">
        {% for c in clusters %}
        <div class="modal-row">
          <input name="name_{{ loop.index0 }}" value="{{ c.name }}" placeholder="Name">
          <input name="hostport_{{ loop.index0 }}" value="{{ c.hostport }}" placeholder="host:port">
          <button type="button" class="cluster-remove" onclick="removeClusterRow(this)">Remove</button>
        </div>
        {% endfor %}
      </div>
      <input type="hidden" name="count" id="clusterCount" value="{{ clusters|length }}">
      <div class="modal-buttons">
        <button type="button" class="cluster-add" onclick="addClusterRow()">+ Add Cluster</button>
      </div>
      <div class="modal-buttons">
        <button type="submit" class="save">Save</button>
        <button type="button" class="cancel" onclick="closeClusterModal()">Cancel</button>
      </div>
    </form>
  </div>
</div>

<!-- Help / Info Modal -->
<div id="helpModal" class="modal">
  <div class="modal-content">
    <h3>API Information</h3>
    <p>This service exposes a simple REST API for accessing and submitting DX spots.</p>
    <h4>GET /spots</h4>
    <p>Returns a JSON list of the cached spots.</p>
    <pre><code>[
  {
    "spotter": "K1ABC",
    "spotted": "DL1XYZ",
    "frequency": "14074",
    "message": "FT8 CQ DX",
    "when": "2025-11-11T18:22:15Z",
    "band": "20m",
    "source": "cluster",
    "dxcc_spotter": {...},
    "dxcc_spotted": {...}
  }
]</code></pre>

    <h4>GET /stats</h4>
    <p>Returns status:</p>
    <pre><code>{
  "entries": 150,
  "connected": true
}</code></pre>

    <h4>POST /sndspot</h4>
    <p>Sends a new DX spot to the connected cluster node.</p>
    <p><b>Request (JSON or form):</b></p>
    <pre><code>{
  "frequency": "14000",
  "callsign": "K1ABC",
  "remarks": "CQ DX via POTA"
}</code></pre>
    <p><b>Response:</b></p>
    <pre><code>{
  "status": "ok"
}</code></pre>
    <div class="modal-buttons">
      <button type="button" class="cancel" onclick="closeHelpModal()">Close</button>
    </div>
  </div>
</div>

<!-- Credential Setup Modal (only appears if credentials missing) -->
{% if not cfg.portal_user or not cfg.portal_pass %}
<div id="credModal" class="modal" style="display:flex;">
  <div class="modal-content">
    <h3>Set Admin Credentials</h3>
    <p>Before continuing, please create login credentials to secure your DXCluster portal.</p>
    <form method="post" action="/portal/setup">
      <div class="modal-row">
        <label>Username:</label>
        <input name="user" required placeholder="Enter username">
      </div>
      <div class="modal-row">
        <label>Password:</label>
        <input name="pass" type="password" required placeholder="Enter password">
      </div>
      <div class="modal-buttons">
        <button type="submit" class="save">Save</button>
      </div>
    </form>
  </div>
</div>
{% endif %}

<script>
// Dropdown control
function toggleDropdown(){ const d=document.getElementById('gearDropdown'); d.style.display = (d.style.display==='block')?'none':'block'; }
function closeDropdown(){ document.getElementById('gearDropdown').style.display='none'; }
window.onclick = function(e){ if(!e.target.matches('.gear')) closeDropdown(); };

// Setup modal
function openSetupModal(){ document.getElementById('setupModal').style.display='flex'; }
function closeSetupModal(){ document.getElementById('setupModal').style.display='none'; }

// Help modal
function openHelpModal(){ document.getElementById('helpModal').style.display='flex'; }
function closeHelpModal(){ document.getElementById('helpModal').style.display='none'; }

// Cluster modal
function openClusterModal(){ document.getElementById('clusterModal').style.display='flex'; }
function closeClusterModal(){ document.getElementById('clusterModal').style.display='none'; }

// Add/remove cluster rows
function addClusterRow(){
  const list=document.getElementById('clusterList');
  const countInput=document.getElementById('clusterCount');
  const idx=parseInt(countInput.value);
  const div=document.createElement('div');
  div.className='modal-row';
  div.innerHTML=`<input name="name_${idx}" placeholder="Name">
                 <input name="hostport_${idx}" placeholder="host:port">
                 <button type="button" onclick="removeClusterRow(this)" style="background:#dc3545;color:white;">Remove</button>`;
  list.appendChild(div);
  countInput.value=idx+1;
}
function removeClusterRow(btn){
  btn.parentElement.remove();
  document.getElementById('clusterCount').value=document.querySelectorAll('#clusterList .modal-row').length;
}

// Restart client
function restartClient(){
  fetch('/restart', {method:'POST'}).then(()=>alert('DXCluster client restarting...')).catch(err=>alert('Error: '+err));
}

// Periodic updates
setInterval(fetchSpots,5000);
setInterval(fetchStatus,5000);

function fetchSpots(){
  fetch('/spots').then(r=>r.json()).then(data=>{
    const term = document.getElementById('spotsTerminal');
    term.innerHTML = '';
    
    // Oldest first, newest last
    data.slice().reverse().forEach(s => {
      const div = document.createElement('div');
      div.className = 'terminal-line';
      div.textContent = `${s.when} | ${s.spotter} -> ${s.spotted} | ${s.frequency}kHz | ${s.band} | ${s.message}`;
      term.appendChild(div);
    });

    // Scroll to bottom
    setTimeout(() => { 
      term.scrollTop = term.scrollHeight; 
    }, 10);
  }).catch(console.error);
}

function fetchStatus(){
  fetch('/stats').then(r=>r.json()).then(data=>{
    const connElem=document.getElementById('connectionStatus');
    const cachedElem=document.getElementById('cachedSpots');
    connElem.innerHTML='DXCluster connection: <span style="color:'+(data.connected?'#28a745':'#dc3545')+'">'+(data.connected?'Connected':'Disconnected')+'</span>';
    cachedElem.innerHTML='Cached spots: '+data.entries;
  }).catch(console.error);
}

function sendSpotJS(event){
    event.preventDefault();
    const freq = document.getElementById('spotFrequency').value;
    const call = document.getElementById('spotCallsign').value;
    const remarks = document.getElementById('spotRemarks').value;

    fetch('/sndspot', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({frequency: freq, callsign: call, remarks: remarks})
    }).then(r=>r.json()).then(data=>{
        const status = document.getElementById('spotStatus');
        if(data.status==='ok'){
            status.textContent='Spot sent successfully!';
            status.style.color='green';
        }else{
            status.textContent='Failed to send spot.';
            status.style.color='red';
        }
    }).catch(err=>{
        const status = document.getElementById('spotStatus');
        status.textContent='Error: '+err;
        status.style.color='red';
    });
    return false;
}
</script>
</body>
</html>
"""


def check_auth(username, password):
    cfg = load_config()
    return (
        username == cfg.get("portal_user") and
        password == cfg.get("portal_pass")
    )

def authenticate():
    return Response(
        'Login required.', 401,
        {'WWW-Authenticate': 'Basic realm="DXCluster Admin"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        cfg = load_config()
        if not cfg.get("portal_user") or not cfg.get("portal_pass"):
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

@app.route("/")
@requires_auth
def portal_index():
    with _lock:
        cfg = load_config()
        entries = len(spots)
        freshest = max((s.get("when") for s in spots), default=None)
        oldest = min((s.get("when") for s in spots), default=None)
        recent = list(reversed(list(spots)))[0:50]
        connected = client.connected
        clusters = load_clusters()
    return render_template_string(
        PORTAL_HTML,
        cfg=cfg,
        entries=entries,
        freshest=freshest,
        oldest=oldest,
        recent=recent,
        connected=connected,
        clusters=clusters
    )


@app.route("/config", methods=["POST"])
@requires_auth
def portal_config():
    form = request.form
    cfg = load_config()
    cluster_hp = form.get("cluster", f"{cfg['host']}:{cfg['port']}")
    if ":" in cluster_hp:
        host, port = cluster_hp.split(":", 1)
        cfg["host"] = host
        cfg["port"] = int(port)
    cfg["call"] = form.get("call", cfg.get("call"))
    cfg["maxcache"] = int(form.get("maxcache", cfg.get("maxcache")))
    cfg["webport"] = int(form.get("webport", cfg.get("webport")))
    cfg["dxcc_lookup_url"] = form.get("dxcc_lookup_url", cfg.get("dxcc_lookup_url"))
    cfg["dxcc_lookup_key"] = form.get("dxcc_lookup_key", cfg.get("dxcc_lookup_key"))
    save_config(cfg)
    with _lock:
        global spots
        spots = deque(list(spots), maxlen=cfg.get("maxcache", 500))
    return redirect(url_for("portal_index"))

@app.route("/clusters/save", methods=["POST"])
@requires_auth
def save_clusters_route():
    count = int(request.form.get("count", 0))
    clusters = []
    for i in range(count):
        name = request.form.get(f"name_{i}", "").strip()
        hostport = request.form.get(f"hostport_{i}", "").strip()
        if name and hostport:
            clusters.append({"name": name, "hostport": hostport})
    save_clusters(clusters)
    return redirect(url_for("portal_index"))


@app.route("/restart", methods=["POST"])
@requires_auth
def portal_restart():
    threading.Thread(target=restart_client, daemon=True).start()
    return redirect(url_for("portal_index"))


def restart_client():
    global client
    with _lock:
        try:
            client.stop()
        except Exception:
            pass
        time.sleep(0.5)
        client = DXClusterClient()
        client.start()

@app.route("/portal/setup", methods=["POST"])
def portal_setup_user():
    user = request.form.get("user", "").strip()
    pwd = request.form.get("pass", "").strip()
    if user and pwd:
        cfg = load_config()
        cfg["portal_user"] = user
        cfg["portal_pass"] = pwd
        save_config(cfg)
        logging.info("Admin credentials saved.")
    return redirect(url_for("portal_index"))

@app.route("/logout")
def portal_logout():
    """Force browser to forget HTTP Basic credentials."""
    resp = Response('Logged out', 401)
    resp.headers['WWW-Authenticate'] = 'Basic realm="DXCluster Admin ' + str(time.time()) + '"'
    return resp

# send telnet command
@app.route("/sendcmd", methods=["POST"])
@requires_auth
def send_telnet_cmd():
    cmd = request.form.get("cmd", "").strip()
    if not cmd:
        return jsonify({"error": "No command provided"}), 400

    with _lock:
        if not client.connected or not client.tn:
            return jsonify({"error": "DXCluster client not connected"}), 400
        try:
            # Append newline if missing
            if not cmd.endswith("\n"):
                cmd += "\n"
            client.tn.write(cmd.encode())
            return jsonify({"status": "sent", "cmd": cmd.strip()})
        except Exception as e:
            logging.error(f"Failed to send telnet command: {e}")
            return jsonify({"error": str(e)}), 500

@app.route("/sndspot", methods=["POST"])
def sndspot():
    data = request.get_json() or request.form
    freq = data.get("frequency")
    call = data.get("callsign")
    remarks = data.get("remarks", "")

    if not freq or not call:
        return jsonify({"error": "frequency and callsign are required"}), 400

    success = send_spot(freq, call, remarks)
    if success:
        return jsonify({"status": "ok"})
    else:
        return jsonify({"status": "failed"}), 500


# -----------------------------
# Startup
# -----------------------------
if __name__ == "__main__":
    save_config(config)
    with _lock:
        spots = deque(list(spots), maxlen=config.get("maxcache", 500))
    client.start()

    try:
        from waitress import serve
        logging.info(f"Starting web server on 0.0.0.0:{config.get('webport')}")
        serve(app, host="0.0.0.0", port=config.get("webport"))
    except Exception:
        app.run(host="0.0.0.0", port=config.get("webport"))
