# Veritas Agent — Kubernetes Deployment Guide

Deploy the Veritas Agent into your Kubernetes cluster in under 5 minutes.

---

## Prerequisites

- Kubernetes cluster (1.20+)
- `kubectl` configured and connected to your cluster
- Veritas Runtime running and accessible from the cluster (e.g. `http://192.168.1.50:8000`)
- Veritas Agent Docker image built and available:
  ```bash
  cd ../   # veritas-agent directory
  docker build -t veritas-agent:latest .
  # If using a private registry:
  docker tag veritas-agent:latest your-registry/veritas-agent:latest
  docker push your-registry/veritas-agent:latest
  ```

---

## Step 1 — Issue a Registration Key

1. Open the Veritas dashboard in your browser
2. Select your organisation from the dropdown
3. Go to **Agents** tab → **Issue Registration Key**
4. Copy the key (it expires in 30 minutes, single-use)

---

## Step 2 — Encode the Key

```bash
# Linux / Mac
echo -n "YOUR_KEY_HERE" | base64

# Windows PowerShell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("YOUR_KEY_HERE"))
```

---

## Step 3 — Fill In the Secret

Edit `agent-secret.yaml` and replace `REPLACE_WITH_BASE64_ENCODED_KEY` with the output from Step 2:

```yaml
data:
  registration_key: eW91ci1rZXktaGVyZQ==   # ← your base64 key
```

---

## Step 4 — Configure the Agent

Edit `agent-configmap.yaml`:

```yaml
veritas_address: http://192.168.1.50:8000   # ← your Veritas Runtime IP
source_label: k8s-production                 # ← label shown in dashboard
sources:
  - type: file
    path: /var/log/order-service/app.log
    source_system: order-service
  - type: docker
    container: marketing-service
    source_system: marketing-analytics
```

The `source_system` values must match what's declared in your Veritas organisation config.

---

## Step 5 — Deploy

### Option A — Single instance (recommended for most setups)

```bash
kubectl apply -f agent-secret.yaml
kubectl apply -f agent-configmap.yaml
kubectl apply -f agent-deployment.yaml
```

### Option B — One agent per node (DaemonSet)

Use this if your application pods run across multiple nodes and you need host-level log collection on each node.

```bash
kubectl apply -f agent-secret.yaml
kubectl apply -f agent-configmap.yaml
kubectl apply -f agent-daemonset.yaml
```

---

## Step 6 — Verify

```bash
# Check the pod is running
kubectl get pods -l app=veritas-agent

# Follow logs
kubectl logs -l app=veritas-agent -f

# Expected output:
# [Veritas] License valid — ...
# [Veritas] Registered as VERITAS-AGENT-XXXXXX
# [Veritas] Forwarding worker started. Watching queue...
```

Within seconds, the agent appears as **ACTIVE** in the Veritas dashboard Agents tab.

---

## Updating the Configuration

```bash
# Edit the ConfigMap
kubectl edit configmap veritas-agent-config

# Restart the agent to pick up changes
kubectl rollout restart deployment/veritas-agent
```

---

## Revoking the Agent

From the Veritas dashboard: **Agents** → find the agent → **Revoke**.

The agent will be rejected with HTTP 403 on its next event submission and log an error. Delete the pod to stop it completely:

```bash
kubectl delete deployment veritas-agent   # or daemonset
kubectl delete pvc veritas-agent-state    # removes saved identity
```

---

## Choosing Between Deployment and DaemonSet

| | Deployment | DaemonSet |
|---|---|---|
| **Instances** | 1 | 1 per node |
| **Best for** | Fixed log files, specific containers | Host-level logs across all nodes |
| **State** | PersistentVolumeClaim | hostPath per node |
| **Registration keys needed** | 1 | 1 per node |
