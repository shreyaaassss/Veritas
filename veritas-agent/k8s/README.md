# Veritas Agent on Kubernetes

One Veritas agent runs on every node (a DaemonSet). Each agent reads the logs of the pods on its node and sends them to your Veritas server, where personal data is detected and compliance rules are applied. **You do not change or restart your applications.**

```
Node 1                         Node 2
  pods: order-..., cart-...      pods: order-..., payments-...
        │ stdout logs                  │ stdout logs
        ▼                              ▼
  veritas-agent (1 per node)     veritas-agent (1 per node)
        └──────────────┬───────────────┘
                       ▼
                 Veritas server
```

The agent is a log bridge: it does not detect or judge anything itself. Only the logs of pods that match a rule you write (see [Choosing which pods to send](#choosing-which-pods-to-send)) are read.

## What you need

- Kubernetes 1.20 or newer, `kubectl` access, and permission to create a namespace.
- A running Veritas server that the cluster's nodes can reach (HTTPS, port 8000), with your organization already created and its policy configured.
- The agent image: `ghcr.io/shreyaaassss/veritas-agent:<version>` (published with each release), or build it yourself from `veritas-agent/` and push it to your registry.
- Container logs written by containerd or CRI-O (the default on EKS, GKE, AKS, k3s, kind) or by Docker's json-file driver. Both line formats are understood.

## Install

### 1. Create a reusable key

In the Veritas dashboard open **Agents → Issue Registration Key**, choose **Many agents (reusable)**, set the maximum number of agents (at least your node count, with some spare for nodes added later) and a validity period, and copy the key. Every node's agent registers itself with this one key.

### 2. Create the namespace and store the key as a Secret

```bash
kubectl apply -f namespace.yaml
kubectl -n veritas-agent create secret generic veritas-agent-key \
  --from-literal=registration_key='<THE KEY>'
```

### 3. Tell the agent where the server is and which pods to read

Edit `agent-configmap.yaml`:

```yaml
veritas_address: https://veritas.yourcompany.internal:8000
...
rules:
  - match: {namespace: shop, pod: "order-*"}
    source_system: order-service
```

Then `kubectl apply -f agent-configmap.yaml`.

If the server is reached by an IP address or a name that its certificate does not list, set `VERITAS_TLS_SANS` on the server (a comma-separated list of extra names and IPs) before its certificate is first generated, or install a certificate that covers it.

### 4. Deploy

```bash
kubectl apply -f agent-daemonset.yaml        # or: kubectl apply -k .
```

Use the manifest attached to the release (`veritas-agent-k8s_<version>.yaml`) to get the image already pinned to that version, or change the `image:` line yourself.

### 5. Check it

```bash
kubectl -n veritas-agent get pods -o wide                       # one Running pod per node
kubectl -n veritas-agent logs ds/veritas-agent --tail=20        # "Registered as VERITAS-AGENT-..."
```

In the dashboard, **Agents** shows one agent per node (labelled `k8s-<node name>`), each with its version and health. A source line such as `12 file(s) followed, 30 ignored (no rule matches)` means it is working.

### 6. Revoke the key when the rollout is done

In **Agents → Enrollment Keys**, revoke the key. Agents that already registered keep working (they hold their own identity). A node added later needs a valid key: issue a new one, update the Secret, and the new node's pod will register.

## Choosing which pods to send

Each node log file is named by the kubelet `<pod>_<namespace>_<container>-<id>.log`. A rule says which files belong to which system:

```yaml
rules:
  - match: {namespace: shop, pod: "order-*"}          # every key must match; * and ? are wildcards
    source_system: order-service
  - match: {namespace: shop, container: "payments*"}
    source_system: payments-service
  - match: {namespace: "team-*"}
    source_system: "{namespace}-{container}"          # placeholders: {namespace} {pod} {container}
```

- The **first** rule that matches wins.
- `source_system` must be a system declared in the organization's policy in Veritas. A system the policy does not declare produces purpose violations for personal data.
- Pods that match **no** rule are not read at all. The agent logs one warning naming a few of them, and the dashboard shows how many files were ignored. This includes the agent's own pods, so there is no feedback loop.
- To see the names to match, run `kubectl get pods -A`.

## What the agent sends and keeps

- It sends each log line of a matched pod: the text, the `source_system` and (via the agent's identity) the organization. Detection and rules run on the Veritas server.
- It keeps an in-memory queue of 1,000 lines while the server is unreachable and drops the oldest lines beyond that. Nothing is written to disk except its identity.
- Lines the container runtime split into pieces (long lines) are joined before being sent.
- Multi-line messages such as stack traces arrive as separate lines.
- Its identity (agent id and token) is stored on the node in `/var/lib/veritas-agent`, owner-only, so restarts and upgrades need no new key.

## Security notes

- The pod runs as root because the kubelet writes container logs as root-only files. The root filesystem is read-only, all Linux capabilities are dropped, privilege escalation is off, and it has no Kubernetes API access. The only writable host path is its own state directory; `/var/log` is mounted read-only.
- The key is a Kubernetes Secret and is only needed for the first registration on each node.

## Upgrading

```bash
kubectl -n veritas-agent set image ds/veritas-agent agent=ghcr.io/shreyaaassss/veritas-agent:<new version>
kubectl -n veritas-agent rollout status ds/veritas-agent
```

## Uninstalling

```bash
kubectl delete namespace veritas-agent
# on each node, remove the saved identity:  rm -rf /var/lib/veritas-agent
```

Then revoke the agents in the dashboard (**Agents → Revoke**).

## Troubleshooting

| What you see | Likely cause and fix |
|---|---|
| A pod ends right after starting (`Error` or `CrashLoopBackOff`) | Read `kubectl -n veritas-agent logs <pod>`. Exit code 78 means a setting retrying cannot fix; the log says which. |
| `Registration rejected ... reached its use limit` | The key was used up. Issue a new reusable key with a higher limit and update the Secret. |
| `Registration rejected ... expired` or `revoked` | Issue a new key and update the Secret. |
| `Config refers to environment variable(s) that are not set` | The Secret or `NODE_NAME` is missing from the pod; check the Secret name and key (`registration_key`). |
| `TLS certificate verification failed` | The server's certificate does not cover the address in `veritas_address`. Add the name or IP with `VERITAS_TLS_SANS` on the server, or use a name the certificate lists. |
| Agents show in the dashboard but no violations appear | The rules match no pods (the source line says `... ignored`), or the `source_system` is not in the organization's policy. Check `kubectl get pods -A` against your rules. |
| `Permission denied` on the logs | Another security layer (such as SELinux or a restricted Pod Security policy) blocks hostPath access. The DaemonSet needs read access to `/var/log` on the node. |
| Docker-runtime nodes: files are listed but empty | `/var/log/containers` holds symlinks into `/var/lib/docker/containers`. Add a read-only hostPath mount of `/var/lib/docker/containers` to the DaemonSet. (Docker as a runtime was removed in Kubernetes 1.24.) |
| Too many files | The agent follows at most 500 files per source; raise `max_files` on the source or tighten the rules. |

## Alternative: one agent for specific files

`agent-deployment.yaml` runs a single agent that reads plain files (wildcards allowed), for example logs your applications write to a shared volume. Change the ConfigMap to `type: file` sources and replace the `logs` volume with yours. For pod logs use the DaemonSet.

## Reading Docker container logs through the Docker socket

A source of `type: docker` still works where the Docker socket is available: mount `/var/run/docker.sock` read-only, set `container: <name>`, and use the `veritas-agent` image with the `docker` Python package (included). Most current clusters have no Docker socket; use `kubernetes_logs` instead.
