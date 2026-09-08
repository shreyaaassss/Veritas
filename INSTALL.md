# Veritas DPDPA Compliance Platform — Installation & Setup Guide

**Version:** 1.0.2  
**Supported OS:** Ubuntu 22.04+, macOS 12+ (Apple Silicon), Windows 10/11

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
9. [Veritas CLI Reference](#veritas-cli-reference)

---

## Overview

Veritas runs entirely on your infrastructure — nothing leaves your network except optional AI investigation calls (which send no raw PII). There are two components:

| Component | Purpose | Installs on |
|---|---|---|
| **Veritas Server** | Compliance dashboard, PII detection engine, evidence store | Your compliance hub (1 machine) |
| **Veritas Agent** | Lightweight log forwarder | Every server you want to monitor |

---

## Before You Install

### System Requirements

| | Server | Agent |
|---|---|---|
| OS | Ubuntu 22.04+ / macOS 12+ / Windows 10+ | Ubuntu 18.04+ / any Linux |
| RAM | 2 GB minimum, 4 GB recommended | 128 MB |
| Disk | 2 GB free | 50 MB |
| Network | Outbound HTTPS (optional, for AI) | Outbound to Veritas server |

### Get Your License

Veritas requires a machine-bound license file (`.vlic`). Before installing:

1. Install Veritas (steps below)
2. Run `sudo veritas fingerprint` — copy the 64-character hash
3. Email the hash to your Veritas contact
4. You will receive a `veritas.vlic` file by email

---

## Linux Installation

### Quick Install (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/shreyaaassss/Veritas/main/install.sh | sudo bash
```

### Manual Install

```bash
# Download the package
wget https://github.com/shreyaaassss/Veritas/releases/download/v1.0.2/veritas_1.0.2_amd64.deb

# Install
sudo dpkg -i veritas_1.0.2_amd64.deb
```

### Verify Installation

```bash
veritas version
# → Veritas 1.0.0
```

### Get Machine Fingerprint (for license)

```bash
sudo veritas fingerprint
```

Output:
```
Veritas Machine Fingerprint
============================================
Send this fingerprint to your Veritas contact
to receive your license file (veritas.vlic):

b25543081e323ffb14bb40ce657fb43805f1094a...

System:   Linux 6.8.0-aws
Hostname: my-server
```

### Install License and Start

```bash
# Install your license file
sudo veritas license /path/to/veritas.vlic

# Start the service
sudo veritas start

# Check status
sudo veritas status
```

### Service Management

```bash
sudo veritas start       # Start
sudo veritas stop        # Stop
sudo veritas restart     # Restart
sudo veritas status      # Show status
sudo veritas logs        # Stream live logs (Ctrl+C to stop)
sudo veritas check       # Run production health checks
```

### Configuration

Edit `/etc/veritas/config.env`:

```bash
sudo nano /etc/veritas/config.env
```

```env
# TLS (recommended for production)
VERITAS_TLS=auto

# AI Investigation — optional (no raw PII is ever sent)
# ANTHROPIC_API_KEY=sk-ant-...
# VERITAS_AI_MODE=external

# Port (default: 8000)
# VERITAS_PORT=8000
```

After changes: `sudo veritas restart`

---

## macOS Installation

### Quick Install (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/shreyaaassss/Veritas/main/install.sh | sudo bash
```

### Manual Install

1. Download `veritas_1.0.2_arm64.pkg` from [GitHub Releases](https://github.com/shreyaaassss/Veritas/releases/tag/v1.0.2)
2. Double-click the `.pkg` file
3. Follow the installer prompts

Or via terminal:

```bash
# Download
curl -L https://github.com/shreyaaassss/Veritas/releases/download/v1.0.2/veritas_1.0.2_arm64.pkg \
  -o /tmp/veritas.pkg

# Install
sudo installer -pkg /tmp/veritas.pkg -target /
```

### Get Machine Fingerprint (for license)

```bash
sudo veritas fingerprint
```

### Install License and Start

```bash
# Install license
sudo veritas license /path/to/veritas.vlic

# Start the service
sudo veritas start
```

### Service Management

```bash
sudo veritas start       # Start (loads LaunchDaemon)
sudo veritas stop        # Stop
sudo veritas restart     # Restart
sudo veritas status      # Show running status
sudo veritas logs        # Tail /var/log/veritas/veritas.log
```

### Logs Location

```
/var/log/veritas/veritas.log
/var/log/veritas/veritas-error.log
```

---

## Windows Installation

### Install

1. Download `VeritasSetup-1.0.0.exe` from your Veritas contact (or GitHub Releases)
2. Double-click the installer
3. Follow the setup wizard — choose install directory (default: `C:\Program Files\Veritas\`)
4. The installer registers Veritas as a Windows Service (auto-start)

### Get Machine Fingerprint (for license)

Open **Command Prompt as Administrator**:

```cmd
cd "C:\Program Files\Veritas"
veritas-fingerprint.exe
```

Output:
```
Veritas Machine Fingerprint
===========================
Send this to support@veritas.io with your order:

b25543081e323ffb14bb40ce657fb43805f1094a...

System:   Windows 11
Hostname: MY-PC
```

### Install License

Copy your `veritas.vlic` to:
```
C:\ProgramData\Veritas\veritas.vlic
```

Then restart the service:

```cmd
net stop VeritasService
net start VeritasService
```

Or via Services panel: `services.msc` → **Veritas DPDPA Platform** → Restart

### Service Management

```cmd
net start VeritasService    # Start
net stop VeritasService     # Stop
```

Or use the Go launcher CLI:

```cmd
veritas-launcher.exe start
veritas-launcher.exe stop
veritas-launcher.exe status
```

### Access Dashboard

Open your browser: **http://localhost:8000**

---

## First-Boot Setup

After starting Veritas for the first time on any platform:

1. Open your browser and go to **http://localhost:8000/setup**
2. Create your administrator account:
   - Username (minimum 3 characters)
   - Email address
   - Password (minimum 8 characters)
3. Click **Create Administrator**
4. Log in at **http://localhost:8000**

> The `/setup` page is only available when no users exist. Once an admin is created, this page returns 403.

### Upload Your Organisation Config

1. Log in to the dashboard
2. Go to **Settings → Org Config**
3. Upload your `org_config.yaml` or use the web form
4. The org config defines which PII fields to monitor, retention policies, and source systems

---

## Agent Deployment

The Veritas Agent is a lightweight Python service (~9 KB package) that reads log files and Docker container logs, forwarding events to the Veritas Server for PII analysis.

> Install one agent on each server you want to monitor.

### Step 1 — Install the Agent

**Ubuntu / Debian:**
```bash
wget https://github.com/shreyaaassss/Veritas/releases/download/v1.0.2/veritas-agent_1.0.2_all.deb
sudo dpkg -i veritas-agent_1.0.2_all.deb
```

The agent requires Python 3.8+ (installed automatically as a dependency).

### Step 2 — Get a Registration Key

On the Veritas dashboard:
1. Go to **Agents** tab
2. Click **Issue Registration Key**
3. Select your org ID
4. Copy the key (format: `XXXXXXXXXXXXXXXXXXXXXXXX`)

### Step 3 — Fetch the Server's TLS Certificate

```bash
sudo veritas-agent fetch-cert https://your-veritas-server:8000
```

This downloads the server's TLS certificate to `/etc/veritas-agent/server.crt` for secure communication.

> If your server runs without TLS (development only), skip this step and set `verify: false` in the config.

### Step 4 — Configure the Agent

```bash
sudo nano /etc/veritas-agent/config.yaml
```

```yaml
# Veritas server address
veritas_address: https://your-veritas-server:8000

# Your organisation ID (must match what's on the server)
org_id: your_org_name

# Registration key from the dashboard → Agents → Issue Key
registration_key: "AkBOPDQygiU4fjZTdXAI4w"

# TLS verification
tls:
  ca_cert: /etc/veritas-agent/server.crt
  verify: true

# Label for this agent (shows in dashboard)
source_label: production-web-server

# Log sources to monitor
sources:
  - type: file
    path: /var/log/app/application.log
    source_system: web-app

  - type: file
    path: /var/log/nginx/access.log
    source_system: nginx

  # Docker container logs
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
# ● veritas-agent.service - Veritas DPDPA Agent
#      Active: active (running)

sudo veritas-agent logs
# Tail live agent logs
```

Within seconds, the agent appears in the Veritas dashboard under the **Agents** tab with status **Active**.

### Agent CLI Reference

```bash
sudo veritas-agent start              # Start agent
sudo veritas-agent stop               # Stop agent
sudo veritas-agent restart            # Restart agent
sudo veritas-agent status             # Show service status
sudo veritas-agent logs               # Stream live logs
sudo veritas-agent fetch-cert <url>   # Download server TLS cert
sudo veritas-agent version            # Print version
```

---

## License Management

### How Licensing Works

1. Veritas computes a **machine fingerprint** — a SHA-256 hash of hardware identifiers (disk serial, MAC address, hostname)
2. You send this fingerprint to Veritas
3. Veritas generates a `.vlic` file signed with an RSA-2048 private key (never leaves Veritas)
4. Your server validates the signature and fingerprint on every start

### Transferring a License to a New Machine

If you replace a server, the fingerprint changes and the old license will not work. Contact your Veritas representative with the new machine's fingerprint to receive a replacement license.

### License File Location

| Platform | Path |
|---|---|
| Linux | `/var/lib/veritas/veritas.vlic` |
| macOS | `/var/lib/veritas/veritas.vlic` |
| Windows | `C:\ProgramData\Veritas\veritas.vlic` |

---

## Veritas CLI Reference

### Server CLI (`veritas`)

| Command | Description |
|---|---|
| `sudo veritas start` | Start the Veritas service |
| `sudo veritas stop` | Stop the Veritas service |
| `sudo veritas restart` | Restart the Veritas service |
| `sudo veritas status` | Show service status |
| `sudo veritas logs` | Stream live service logs |
| `sudo veritas fingerprint` | Print machine fingerprint for license generation |
| `sudo veritas license <file>` | Install a `.vlic` license file and restart |
| `sudo veritas check` | Run production acceptance checks |
| `veritas version` | Print version |

### Agent CLI (`veritas-agent`)

| Command | Description |
|---|---|
| `sudo veritas-agent start` | Start the agent |
| `sudo veritas-agent stop` | Stop the agent |
| `sudo veritas-agent restart` | Restart the agent |
| `sudo veritas-agent status` | Show service status |
| `sudo veritas-agent logs` | Stream live agent logs |
| `sudo veritas-agent fetch-cert <url>` | Download server TLS certificate |
| `veritas-agent version` | Print version |

---

## Troubleshooting

### Server won't start — "License not found"
Run `sudo veritas fingerprint`, send the output to your Veritas contact, and install the received `.vlic` with `sudo veritas license /path/to/veritas.vlic`.

### Server won't start — "This license was issued for a different server"
The machine fingerprint has changed (new hardware, VM migration). Request a new license with the current fingerprint.

### Dashboard not accessible
Check the service is running: `sudo veritas status`  
Check the port: `ss -tlnp | grep 8000`  
Check logs: `sudo veritas logs`

### Agent not appearing in dashboard
1. Check agent is running: `sudo veritas-agent status`
2. Check config has correct `veritas_address` and `registration_key`
3. Check TLS cert: `sudo veritas-agent fetch-cert https://your-server:8000`
4. View agent logs: `sudo veritas-agent logs`

### Agent registration key already used
Each key can only be used once. Issue a new key from the dashboard → **Agents** → **Issue Key**.

---

*Veritas DPDPA Compliance Platform · v1.0.2 · For support, contact your Veritas representative.*
