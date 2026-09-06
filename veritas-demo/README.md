# Veritas Demo — Simulated Production Environment

Four simulated services generate realistic log traffic for the Veritas demo.
The Veritas Agent tails the logs and forwards each line to the appliance,
producing live violations on the dashboard.

## Violation Types Demonstrated

| Service | source_system | Violation | Trigger |
|---------|--------------|-----------|---------|
| order-service | `order-service` | EXPOSURE_001 | Raw name + phone + address in DEBUG log |
| support-service | `support-ticketing` | EXPOSURE_001 | Customer PII in support agent note |
| marketing-service | `marketing-analytics` | PURPOSE_001 | Raw phone in deidentified-only scope |
| delivery-service | `delivery-partner-service` | RETENTION_001 | DP-4471 aadhaar 240 days old (> 180-day limit) |

Expected: ~1 violation every 20-25 seconds at default rates.

---

## Setup

### Prerequisites
- Docker + Docker Compose installed
- Veritas is running (`python run_pipeline.py` or via systemd)
- The `veritas-agent:latest` Docker image is built

### Step 1 — Build the Veritas Agent image (first time only)
```bash
cd ../veritas-agent
docker build -t veritas-agent:latest .
cd ../veritas-demo
```

### Step 2 — Issue a registration key
1. Open the Veritas dashboard in your browser
2. Select or create the **blinkit** organisation
3. Go to the **Agents** tab
4. Click **Issue Registration Key**
5. Copy the key

### Step 3 — Configure the agent
```bash
cp agent-config.yaml.example agent-config.yaml
```
Edit `agent-config.yaml`:
- Set `veritas_address` to the IP/hostname of your Veritas machine
- Paste the registration key into `registration_key`

### Step 4 — Start the simulation
```bash
docker compose up --build
```

### Step 5 — Watch violations appear
Open `http://<veritas-ip>:8000` and go to the **Live Stream** tab.
Violations should appear within 20-25 seconds.

---

## Adjusting Traffic

Override rates and intervals via environment variables in `docker-compose.yml`:

| Variable | Default | Effect |
|----------|---------|--------|
| `VIOLATION_RATE` | 0.10 or 0.20 | Fraction of lines that are violations (0.0–1.0) |
| `EMIT_INTERVAL` | 2.0–5.0 | Seconds between log lines |

To increase violation frequency for a demo, set `VIOLATION_RATE=0.5` and `EMIT_INTERVAL=1.0`.

---

## Stopping

```bash
docker compose down
```

The agent's registration state is preserved in the `agent-state` volume.
On the next `docker compose up`, the agent re-uses its existing identity.

To reset and re-register:
```bash
docker compose down -v   # removes volumes including agent state
```
Then issue a new registration key from the dashboard before restarting.
