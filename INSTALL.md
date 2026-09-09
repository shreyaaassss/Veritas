# Veritas DPDPA Compliance Platform — Installation & Setup Guide

**Version:** 1.0.12  
**Supported OS:** Ubuntu 22.04+, macOS 12+ (Apple Silicon), Windows Server 2019/2022 / Windows 10/11

---

## Table of Contents

1. [Overview](#overview)
2. [Before You Install](#before-you-install)
3. [Linux Installation (Ubuntu / Debian)](#linux-installation)
4. [macOS Installation](#macos-installation)
5. [Windows Installation](#windows-installation)
6. [First-Boot Setup](#first-boot-setup)
7. [Agent Deployment](#agent-deployment)
8. [License Management](#license-management)
9. [CLI Reference](#cli-reference)
10. [Troubleshooting](#troubleshooting)

---

## Overview

Veritas runs entirely on your infrastructure — no data leaves your network except optional AI investigation calls (which send no raw PII). Two components:

| Component | Purpose | Installs on |
|---|---|---|
| **Veritas Server** | Compliance dashboard, PII detection, evidence store | Your compliance hub (1 machine) |
| **Veritas Agent** | Lightweight log forwarder | Every server you want to monitor |

---

## Before You Install

### System Requirements

| | Server | Agent |
|---|---|---|
| **OS** | Ubuntu 22.04+ / macOS 12+ / Windows 10+ | Ubuntu 18.04+ or any Linux |
| **RAM** | 2 GB minimum, 4 GB recommended | 128 MB |
| **Disk** | 2 GB free | 50 MB |
| **Network** | Outbound HTTPS (optional, for AI) | Outbound to Veritas server |

### Get Your License

Veritas requires a machine-bound license. Workflow:

1. Install Veritas (steps below)
2. Run `sudo veritas fingerprint` (Linux/macOS) or `veritas fingerprint` (Windows)
3. Send the 64-character hash to your Veritas contact
4. You receive a `veritas.vlic` file
5. Install it with `sudo veritas license /path/to/veritas.vlic`

---

## Linux Installation

### Quick Install

```bash
curl -sSL https://raw.githubusercontent.com/shreyaaassss/Veritas/main/install.sh | sudo bash
```

### Manual Install

```bash
wget https://github.com/shreyaaassss/Veritas/releases/download/v1.0.12/veritas_1.0.12_amd64.deb
sudo dpkg -i veritas_1.0.12_amd64.deb
```

### Complete Setup Flow

```bash
# 1. Get machine fingerprint — send output to your Veritas contact
sudo veritas fingerprint

# 2. Install your license file (received from Veritas contact)
sudo veritas license /path/to/veritas.vlic

# 3. Start the service
sudo veritas start

# 4. Check status
sudo veritas status

# 5. Open dashboard in browser
# https://localhost:8000/setup  (first time — create admin account)
# https://localhost:8000        (after setup)
```

### Service Management

```bash
sudo veritas start       # Start
sudo veritas stop        # Stop
sudo veritas restart     # Restart
sudo veritas status      # Status
sudo veritas logs        # Stream live logs (Ctrl+C to stop)
sudo veritas check       # Run health checks
```

### Configuration

Edit `/etc/veritas/config.env` then restart:

```bash
sudo nano /etc/veritas/config.env
sudo veritas restart
```

```env
# TLS (auto-generated self-signed cert by default)
# VERITAS_TLS=false       # Disable TLS (dev only)

# AI Investigation (optional — no raw PII is ever sent)
# ANTHROPIC_API_KEY=sk-ant-...
# VERITAS_AI_MODE=external

# Port (default: 8000)
# VERITAS_PORT=8000
```

### Accessing the Dashboard

The server runs on HTTPS with a self-signed certificate.

- In Chrome: navigate to `https://localhost:8000` → type **`thisisunsafe`** on the warning page
- In Firefox: click **Advanced → Accept Risk**
- In Safari: click **Show Details → visit this website**

---

## macOS Installation

### Quick Install

```bash
curl -sSL https://raw.githubusercontent.com/shreyaaassss/Veritas/main/install.sh | sudo bash
```

### Manual Install

```bash
# Download
curl -L https://github.com/shreyaaassss/Veritas/releases/download/v1.0.12/veritas_1.0.12_arm64.pkg \
  -o /tmp/veritas.pkg

# Install (requires admin password)
sudo installer -pkg /tmp/veritas.pkg -target /
```

Or double-click `veritas_1.0.12_arm64.pkg` in Finder.

### Complete Setup Flow

```bash
# 1. Get machine fingerprint
sudo veritas fingerprint

# 2. Install license
sudo veritas license ~/Downloads/veritas.vlic

# 3. Start service
sudo veritas start

# 4. Check status
sudo veritas status

# 5. Open dashboard
open https://localhost:8000/setup
```

### Service Management

```bash
sudo veritas start       # Start (launchctl bootstrap)
sudo veritas stop        # Stop
sudo veritas restart     # Restart
sudo veritas status      # Status
sudo veritas logs        # Tail service log
```

### Logs Location

```
/var/log/veritas/veritas.log        # stdout
/var/log/veritas/veritas-error.log  # stderr (includes startup messages)
```

---

## Windows Installation

### Install

1. Download **`veritas_1.0.12_windows_amd64.exe`** from [GitHub Releases](https://github.com/shreyaaassss/Veritas/releases/latest)
2. Right-click → **Run as Administrator**
3. Follow the setup wizard:
   - Choose install directory (default: `C:\Program Files\Veritas\`)
   - Select your `.vlic` license file when prompted *(or skip and install later)*
4. The installer registers Veritas as a Windows Service (auto-start on boot)

> **Note:** If you skip the license during install, place your `.vlic` file at
> `C:\Program Files\Veritas\veritas.vlic` then restart the service.

### Get Machine Fingerprint

Open **Command Prompt as Administrator**:

```cmd
"C:\Program Files\Veritas\veritas-launcher.exe" fingerprint
```

Or run the standalone fingerprint tool:

```powershell
python tools\fingerprint.py
```

### Install License After Setup

```powershell
# Copy license to install directory
Copy-Item "C:\path\to\veritas.vlic" "C:\Program Files\Veritas\veritas.vlic"

# Restart the service
Restart-Service -Name "VeritasService"
```

### Service Management

Via **Services panel** (`services.msc`): look for **Veritas DPDPA Platform**

Or via command line (as Administrator):

```cmd
net start VeritasService
net stop VeritasService
```

Or via the Go launcher:

```cmd
cd "C:\Program Files\Veritas"
veritas-launcher.exe start
veritas-launcher.exe stop
veritas-launcher.exe status
```

### Access Dashboard

Open your browser: **http://localhost:8000/setup**

> Windows uses HTTP by default. TLS can be enabled by setting `VERITAS_TLS=true`
> in the environment before starting the service.

### Logs Location

```
C:\Program Files\Veritas\veritas.log
C:\Program Files\Veritas\veritas-error.log
```

Or view via Windows Event Viewer → Application logs.

---

## First-Boot Setup

After starting Veritas for the first time on any platform:

1. Open **https://localhost:8000/setup** (Linux/macOS) or **http://localhost:8000/setup** (Windows)
2. Create your administrator account:
   - Username (min 3 characters)
   - Email address
   - Password (min 8 characters)
3. Click **Create Administrator**
4. Log in at `https://localhost:8000`

> The `/setup` page is only available when no users exist. Once an admin is created, this page returns a redirect to the login page.

### Upload Your Organisation Config

1. Log in → **Settings → Org Config**
2. Upload your `org_config.yaml` defining which fields to monitor, retention policies, and source systems

---

## Agent Deployment

Install the Veritas Agent on each server you want to monitor. The agent is a lightweight log forwarder (~9 KB package, no ML models).

### Step 1 — Install the Agent

**Ubuntu / Debian:**

```bash
wget https://github.com/shreyaaassss/Veritas/releases/download/v1.0.12/veritas-agent_1.0.12_all.deb
sudo dpkg -i veritas-agent_1.0.12_all.deb
```

Dependencies (`python3`, `python3-requests`, `python3-yaml`) are installed automatically.

### Step 2 — Issue a Registration Key

On the Veritas **dashboard**:
1. Go to **Agents** tab
2. Click **Issue Registration Key**
3. Select your org ID
4. Copy the key (format: `XXXXXXXXXXXXXXXXXXXXXXXX`, one-time use)

### Step 3 — Fetch the Server TLS Certificate

```bash
sudo veritas-agent fetch-cert https://your-veritas-server:8000
```

This downloads the server certificate to `/etc/veritas-agent/server.crt`.

> Skip if your server runs without TLS and set `verify: false` in the config.

### Step 4 — Configure the Agent

```bash
sudo nano /etc/veritas-agent/config.yaml
```

```yaml
# Veritas server address
veritas_address: https://your-veritas-server:8000

# Your organisation ID (must match what's configured on the server)
org_id: your_org_name

# Registration key from the dashboard (one-time use, consumed on first connect)
registration_key: "AkBOPDQygiU4fjZTdXAI4w"

# TLS verification
tls:
  ca_cert: /etc/veritas-agent/server.crt
  verify: true

# Label shown in the dashboard
source_label: production-web-server

# Log sources to monitor
sources:
  - type: file
    path: /var/log/app/application.log
    source_system: web-app

  - type: file
    path: /var/log/nginx/access.log
    source_system: nginx

  # Docker container logs:
  # - type: docker
  #   container: my-api-container
  #   source_system: api-service
```

### Step 5 — Start the Agent

```bash
sudo veritas-agent start
```

### Step 6 — Verify

```bash
sudo veritas-agent status
sudo veritas-agent logs
```

The agent appears in the Veritas dashboard under **Agents** within seconds.

---

## License Management

### How Licensing Works

1. Veritas computes a **machine fingerprint** — SHA-256 of disk serial + MAC address + hostname
2. You send the fingerprint to Veritas
3. Veritas signs a `.vlic` file with an RSA-2048 private key (never leaves Veritas)
4. The binary validates the signature and fingerprint on every startup

### License File Locations

| Platform | Path |
|---|---|
| Linux | `/var/lib/veritas/veritas.vlic` |
| macOS | `/var/lib/veritas/veritas.vlic` |
| Windows | `C:\Program Files\Veritas\veritas.vlic` |

### Transferring a License (New Machine)

The fingerprint changes if you replace hardware or migrate to a new VM. Contact your Veritas representative with the new machine's fingerprint to receive a replacement license.

---

## CLI Reference

### Server CLI (`veritas`)

| Command | Description |
|---|---|
| `sudo veritas start` | Start the service |
| `sudo veritas stop` | Stop the service |
| `sudo veritas restart` | Restart the service |
| `sudo veritas status` | Show service status |
| `sudo veritas logs` | Stream live logs |
| `sudo veritas fingerprint` | Print machine fingerprint for license |
| `sudo veritas license <file>` | Install a `.vlic` file and restart |
| `sudo veritas check` | Run production acceptance checks |
| `veritas version` | Print version |

### Agent CLI (`veritas-agent`)

| Command | Description |
|---|---|
| `sudo veritas-agent start` | Start the agent |
| `sudo veritas-agent stop` | Stop the agent |
| `sudo veritas-agent restart` | Restart the agent |
| `sudo veritas-agent status` | Show service status |
| `sudo veritas-agent logs` | Stream live logs |
| `sudo veritas-agent fetch-cert <url>` | Download server TLS certificate |
| `veritas-agent version` | Print version |

---

## Troubleshooting

### "License not found" at startup

Run `sudo veritas fingerprint`, send to your Veritas contact, install the `.vlic`:
```bash
sudo veritas license /path/to/veritas.vlic
```

### "This license was issued for a different server"

The machine fingerprint changed (hardware change, VM migration). Request a new license with the current fingerprint from `sudo veritas fingerprint`.

### Dashboard not loading (browser shows security warning)

The server uses a self-signed TLS certificate. Accept it in your browser:
- **Chrome:** type `thisisunsafe` on the warning page
- **Firefox:** Advanced → Accept Risk
- **Safari:** Show Details → Visit Website

### Dashboard returns 500 on /setup or /login

You are likely running an older version. Update to v1.0.12+:
```bash
curl -sSL https://raw.githubusercontent.com/shreyaaassss/Veritas/main/install.sh | sudo bash
```

### PII scan returns 500 Internal Server Error

Update to v1.0.12+. Earlier versions had a missing `presidio_analyzer/conf/` directory in the PyInstaller bundle.

### Agent not appearing in dashboard

1. `sudo veritas-agent status` — is the agent running?
2. Check config: `cat /etc/veritas-agent/config.yaml` — correct `veritas_address` and `registration_key`?
3. Registration keys are **one-time use**. Issue a new key from the dashboard if needed.
4. `sudo veritas-agent fetch-cert https://your-server:8000` — refresh TLS cert
5. `sudo veritas-agent logs` — check for connection errors

### Linux: Service crashes with "Could not create temporary directory"

This is fixed in v1.0.7+. If upgrading from an older version:
```bash
sudo mkdir -p /etc/systemd/system/veritas.service.d
sudo tee /etc/systemd/system/veritas.service.d/override.conf <<EOF
[Service]
Environment=TMPDIR=/var/lib/veritas/tmp
ProtectSystem=false
EOF
sudo systemctl daemon-reload && sudo systemctl restart veritas
```

### License file permissions error (Linux/macOS)

```bash
sudo chown veritas:veritas /var/lib/veritas/veritas.vlic   # Linux
sudo chown _veritas:staff  /var/lib/veritas/veritas.vlic   # macOS
sudo chmod 640 /var/lib/veritas/veritas.vlic
sudo systemctl restart veritas   # Linux
sudo veritas restart             # macOS
```

---

*Veritas DPDPA Compliance Platform · v1.0.12 · For support, contact your Veritas representative.*
