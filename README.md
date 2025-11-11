# 📡 DXCluster Cache

**DXCluster Cache** is a lightweight Python service that connects to a **DX Cluster** and parses real-time DX spots, caches them in memory, enriches them with DXCC lookup data, and provides a **web portal** + **REST API** for viewing and managing spot data.

It is designed for amateur radio operators, logging servers (e.g. **WaveLog**, **CloubLog** integration).

---

## 🚀 Features

* **DX Cluster connection**
  Connects to any standard DXCluster node and parses incoming spots.

* **Spot Caching**
  Maintains a rotating cache (default 500 spots) of the most recent DX spots in memory.

* **DXCC Lookup Integration**
  Uses a configurable HTTP API (e.g., WaveLog endpoint) to enrich each spot with DXCC, CQ zone, and LoTW info, with caching to minimize lookups.

* **Built-in Web Portal**
  Access recent spots in a live “terminal-style” view.
  Includes configuration, DXCluster selection, and sending new spots.

* **REST API Endpoints**

  * `GET /spots/` → All cached spots (JSON)
  * `GET /spots/<band>` → Filter by band
  * `GET /spot/<freq>` → Get the latest spot for a specific frequency
  * `GET /stats` → Connection and cache statistics
  * `POST /sndspot` → Submit a new spot

* **Admin Portal with Authentication**

  * HTTP Basic Auth (username/password)
  * Portal setup modal if credentials are missing on dxcluster_config.json
  * Restart cluster client from the web UI

* **Systemd Service Support**
  Includes a `.service` file for automatic startup and recovery on Linux systems.

---

## 📁 Repository Structure

```
/opt/dxcluster_cache/
├── dxcluster_cache.py         # Main application
├── dxcluster_cache.service    # systemd unit file
├── dxcluster_config.json      # Configuration file (auto-created)
├── clusters.txt               # List of available DX cluster nodes
├── requirements.txt           # Python dependencies
└── README.md                  # Documentation
```

---

## ⚙️ Configuration Files

### `dxcluster_config.json`

This file stores the runtime configuration and is automatically created on first run.

Example:

```json
{
  "host": "dxc.example.org",
  "port": 7300,
  "call": "N0CALL",
  "maxcache": 500,
  "webport": 8000,
  "dxcc_lookup_url": "https://wavelog.example/api/lookup",
  "dxcc_lookup_key": "api-key-goes-here",
  "portal_user": "admin",
  "portal_pass": "secret"
}
```

---

### `clusters.txt`

Defines a list of known cluster nodes for easy switching via the web portal.

Example:

```
ClusterA, dxc1.example.org:7300
ClusterB, dxc2.example.org:7300
```

You can edit this file manually or through the **“Edit Clusters”** dialog in the web UI.

---

## 🧩 REST API Reference

| Endpoint        | Method | Description                                          |
| --------------- | ------ | ---------------------------------------------------- |
| `/spots/`       | GET    | Returns all cached spots                             |
| `/spots/<band>` | GET    | Returns spots for a specific band (e.g., 20m)        |
| `/spot/<freq>`  | GET    | Returns the most recent spot for a frequency         |
| `/stats`        | GET    | Returns status (connected, cache count)              |
| `/sndspot`      | POST   | Send a new spot (`frequency`, `callsign`, `remarks`) |
| `/sendcmd`      | POST   | Send raw Telnet command (admin only)                 |

---

## 💻 Web Portal

The built-in web interface runs on the configured port (default **8000**):

```
http://localhost:8000
```

### Portal Features:

* View recent DX spots in a live-updating terminal view
* Send new DX spots
* Edit service configuration
* Manage cluster list
* Restart Telnet client
* View API documentation
* Admin authentication via HTTP Basic Auth

---

## 🧱 Installation Instructions

### 1. Install Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

### 2. Clone from Git:

```bash
sudo git clone https://github.com/yourusername/dxcluster_cache.git /opt/dxcluster_cache
cd /opt/dxcluster_cache
```

### 3. Install Python Requirements

```bash
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

### 4. Verify the Script Runs

```bash
python3 dxcluster_cache.py
```

Open in your browser:

```
http://localhost:8000
```

If credentials are missing, the portal will prompt you to create them.

---

## 🔁 Install as a Systemd Service

### 1. Copy the Service File

```bash
sudo cp dxcluster_cache.service /etc/systemd/system/
```

### 2. Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable dxcluster_cache
sudo systemctl start dxcluster_cache
```

### 3. Check Status

```bash
sudo systemctl status dxcluster_cache
```

### 4. View Logs

```bash
sudo journalctl -u dxcluster_cache -f
```

---

## 🛠️ Updating the Service

When updating the code or configuration:

```bash
cd /opt/dxcluster_cache
sudo systemctl stop dxcluster_cache
git pull        # or copy new files
sudo systemctl start dxcluster_cache
```

To reload configuration without full restart:
Use the **“Restart DXCluster Client”** button in the web portal.

---

## Example API Usage

### Get all spots

```bash
curl http://localhost:8000/spots/
```

### Get 20m band spots

```bash
curl http://localhost:8000/spots/20m
```

### Send a new spot

```bash
curl -X POST http://localhost:8000/sndspot \
     -H "Content-Type: application/json" \
     -d '{"frequency":"14074","callsign":"K1ABC","remarks":"CQ DX POTA"}'
```

---

## 🧾 License

This project is distributed under the **MIT License**.
You are free to modify and use it for both personal and commercial purposes.

---
