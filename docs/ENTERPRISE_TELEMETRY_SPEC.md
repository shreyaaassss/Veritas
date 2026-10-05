# Veritas: Enterprise Telemetry, Agent & Compliance Flow

## Purpose

This document defines the intended enterprise deployment and telemetry flow for Veritas.

Use this as the implementation reference for the coding agent. The goal is to make Veritas an enterprise-deployable DPDPA compliance platform that runs inside a customer's infrastructure and continuously analyzes telemetry for sensitive-data exposure and policy violations.

Implementation plan: see `IMPLEMENTATION_ORDER.md`, Milestone 4.

## 1. Core Product Model

Veritas consists of two main components:

1. **Veritas Agent**
   - Small telemetry collection and forwarding component.
   - Runs close to the customer's applications/data sources.
   - Collects logs/telemetry.
   - Buffers when necessary.
   - Authenticates with Veritas.
   - Forwards telemetry securely.
   - Does NOT contain the main DPDPA compliance intelligence.

2. **Veritas Core**
   - The main compliance product.
   - Receives telemetry from Agents.
   - Detects personal/sensitive data.
   - Evaluates DPDPA policies.
   - Generates violations.
   - Creates tamper-evident evidence.
   - Provides dashboard, cases, investigation, audit and reporting.
   - May optionally sanitize/redact telemetry before forwarding it to customer storage.

The key design principle is:

> Keep the Agent lightweight and operationally simple. Keep compliance intelligence centralized in Veritas Core.

## 2. Customer Onboarding Flow

When a customer first opens Veritas:

```
Open Veritas Dashboard
        ↓
Dashboard is blank / no organisation configured
        ↓
Create / Register Organisation
        ↓
Organisation exists
        ↓
Configure organisation policy / data inventory
        ↓
Issue one-time Agent Registration Key
        ↓
Deploy Veritas Agent
        ↓
Agent registers itself
        ↓
Agent receives permanent identity/authentication details
        ↓
Agent starts forwarding telemetry
```

The blank dashboard on first startup is intentional.

Do not pre-populate fake organisations, agents, violations or telemetry.

## 3. Agent Registration

The Agent should not require the customer to manually hardcode its permanent Agent ID, organisation ID, token and final event endpoint.

Initial Agent configuration should contain only the bootstrap information required to contact Veritas, for example:

```yaml
veritas_address: http://<veritas-address>:8000
registration_key: <one-time-registration-key>
```

The Agent performs:

```
POST /agent/register
```

The Veritas server validates the one-time registration key.

If valid, Veritas returns the Agent's permanent registration information, such as:

- Agent ID
- Organisation ID
- Authentication token
- Final telemetry/event endpoint

The Agent stores this information securely and uses it for normal operation.

The registration key should be:

- one-time use
- organisation-scoped
- revocable if supported
- never reused as the Agent's permanent authentication credential

## 4. Kubernetes Deployment

Kubernetes should NOT be modeled as:

> "Install one Veritas Agent inside every application pod."

Instead, the preferred Kubernetes deployment model is a DaemonSet or equivalent node-level collector.

Conceptually:

```
Kubernetes Cluster

Node 1                         Node 2
────────                       ────────
Application A                  Application C
Application B                  Application D
     │                              │
     ▼                              ▼
Veritas Agent                  Veritas Agent
(DaemonSet)                    (DaemonSet)
     │                              │
     └──────────────┬───────────────┘
                    ▼
              Veritas Core
```

Each Agent instance runs close to the workloads on its node and collects the relevant telemetry/logs.

Typical Kubernetes sources may include:

- container logs
- node/application log files
- configured application telemetry
- HTTP telemetry where explicitly supported

The implementation should avoid requiring application developers to modify every application just to install the compliance agent.

A Kubernetes Agent can collect container logs from the node-level log locations exposed to the DaemonSet.

## 5. Non-Kubernetes Deployment

Veritas must also support organisations that do not use Kubernetes.

A common startup/SMB/VM/server deployment could look like:

```
Linux Server / VM

Application
     │
     ├── application logs
     │
     ▼
/var/log/app.log
     │
     ▼
Veritas Agent
     │
     ▼
Veritas Core
```

The Agent should be capable of watching configured log files.

For example:

```yaml
sources:
  - type: file
    path: /var/log/app.log
```

The exact configuration format can evolve, but the conceptual behavior is:

```
tail/read configured log source
        ↓
detect new telemetry
        ↓
buffer if necessary
        ↓
authenticate
        ↓
forward to Veritas
```

The Agent should be deployable as an operating-system service where appropriate.

Examples:

- Linux VM/server → system service
- Windows server → Windows service
- Docker host → container/service
- Kubernetes → DaemonSet

Do NOT make Kubernetes a prerequisite for Veritas.

## 6. Agent Responsibilities

The Agent should remain intentionally lightweight.

### Agent SHOULD do

- Read configured telemetry sources.
- Tail log files.
- Collect container logs where configured.
- Accept supported telemetry inputs.
- Buffer temporarily during network/server outages.
- Retry failed transmissions.
- Authenticate with Veritas.
- Send heartbeats.
- Maintain its registration identity.
- Forward telemetry securely.
- Report basic health/status.

### Agent SHOULD NOT do

The Agent should NOT contain the main compliance intelligence.

Do not move the following into the Agent unless there is a specific future architecture reason:

- DPDPA rule evaluation
- Exposure rule
- Purpose rule
- Retention rule
- Linkage rule
- Case management
- Evidence generation
- Hash-chain management
- Investigation engine
- Dashboard logic
- Organisation policy interpretation
- Compliance reporting

The Agent is a telemetry bridge.

## 7. Veritas Core Responsibilities

Veritas Core is the main product.

Its pipeline should conceptually be:

```
Telemetry
    ↓
Ingestion
    ↓
PII / Sensitive Data Detection
    ↓
DPDPA Policy Evaluation
    ↓
Violation Generation
    ↓
Tamper-Evident Evidence
    ↓
Dashboard / Cases / Investigation / Audit
```

Current detection includes:

- names
- phone numbers
- email addresses
- Aadhaar
- PAN

Aadhaar and PAN should continue to use validation rules to reduce false positives.

Current compliance rules:

1. Exposure
2. Purpose
3. Retention
4. Linkage

## 8. What Veritas Does With Customer Data

IMPORTANT:

Do not define Veritas as inherently:

> "a masking proxy that receives everything, masks everything, and becomes the customer's log-storage system."

That is too restrictive and creates unnecessary architectural coupling.

Instead:

> Veritas inspects customer telemetry for sensitive data and DPDPA compliance violations. Sanitization/redaction and downstream forwarding should be configurable behavior.

There are several legitimate deployment modes.

### Mode A: Inspect + Forward Original

```
Customer Source
      ↓
Veritas Agent
      ↓
Veritas Core
      ↓
Compliance analysis
      ↓
Customer logging/SIEM/storage
```

Use when the customer's existing logging system needs the original telemetry.

### Mode B: Inspect + Sanitize + Forward

```
Customer Source
      ↓
Veritas Agent
      ↓
Veritas Core
      ↓
PII detection
      ↓
Redaction / masking
      ↓
Customer storage/SIEM
```

Example:

Before:

```
User email: rahul@example.com
Phone: 9876543210
```

After:

```
User email: [EMAIL]
Phone: [PHONE]
```

This should be configurable rather than mandatory for every customer.

### Mode C: Inspect Without Becoming the Log Store

```
                 ┌──────────────→ Customer SIEM / Log Storage
                 │
Customer Source ─┤
                 │
                 └──────────────→ Veritas Agent → Veritas Core
                                               ↓
                                         Compliance analysis
                                               ↓
                                         Evidence / cases
```

This mode is important because Veritas should not accidentally position itself as a replacement for general-purpose observability/logging platforms.

The product is a DPDPA compliance platform.

## 9. Recommended Enterprise Architecture

The overall architecture should be:

```
                    CUSTOMER INFRASTRUCTURE

┌─────────────────────────────────────────────────────────┐
│                                                         │
│  Applications / APIs / Services / Logs                  │
│                     │                                   │
│                     ▼                                   │
│              ┌───────────────┐                          │
│              │ VERITAS AGENT │                          │
│              │               │                          │
│              │ Collect       │                          │
│              │ Buffer        │                          │
│              │ Authenticate  │                          │
│              │ Forward       │                          │
│              └───────┬───────┘                          │
│                      │                                  │
└──────────────────────┼──────────────────────────────────┘
                       │
                       ▼
              ┌───────────────────┐
              │   VERITAS CORE    │
              │                   │
              │ Ingestion         │
              │       ↓           │
              │ PII Detection     │
              │       ↓           │
              │ DPDPA Rules       │
              │       ↓           │
              │ Violations        │
              │       ↓           │
              │ Evidence / Hash   │
              │       ↓           │
              │ Dashboard/Cases   │
              └─────────┬─────────┘
                        │
                ┌───────┴────────┐
                ▼                ▼
        Compliance Evidence   Optional
                              Sanitization
                                   │
                                   ▼
                         Customer Storage / SIEM
```

## 10. Deployment Forms

Veritas should be positioned as enterprise software, not as a Raspberry Pi product.

Supported deployment targets can include:

### Primary

- VM
- Linux server
- Kubernetes
- Customer-managed infrastructure

### Optional physical appliance

A dedicated physical Veritas Appliance can be offered later.

Raspberry Pi is currently a useful:

- development target
- reference appliance
- physical prototype
- edge deployment demonstration

It must NOT become a requirement for the product.

The product definition remains:

> Veritas is licensed enterprise DPDPA compliance software deployed inside the customer's infrastructure.

## 11. Customer Deployment Experience

The intended experience should feel like:

```
Purchase Veritas License
        ↓
Receive Veritas Package
        ↓
Deploy Veritas
        ↓
Open Setup / Admin Dashboard
        ↓
Create Organisation
        ↓
Configure Policy / Data Inventory
        ↓
Generate Agent Registration Key
        ↓
Deploy Agent
        ↓
Agent Registers Automatically
        ↓
Telemetry Appears
        ↓
Compliance Monitoring Begins
```

Avoid making the customer-facing story:

```
Buy Raspberry Pi
↓
Install Docker
↓
Run docker-compose
↓
Manually configure tokens
```

Docker/Compose/systemd/etc. are implementation mechanisms, not the product story.

## 12. Security Expectations

The Agent-to-Veritas connection should be authenticated.

Minimum expectations:

- One-time bootstrap registration key
- Permanent Agent authentication token after registration
- Organisation isolation
- TLS for production communication
- Revocable Agent credentials where supported
- Secure credential storage
- Agent heartbeat
- Registration status
- Retry/backoff
- No cross-organisation access

The Agent must never be able to access another organisation's telemetry.

## 13. Reliability Expectations

The Agent should tolerate temporary Veritas outages.

Expected behavior:

```
Telemetry arrives
      ↓
Veritas unavailable?
      │
     YES
      ↓
Buffer locally
      ↓
Retry with backoff
      ↓
Veritas available
      ↓
Forward buffered telemetry
```

The implementation should include bounded buffering so an extended outage cannot cause unlimited local disk/memory growth.

Veritas Core should also eventually use an ingestion queue when measured traffic demonstrates the need.

## 14. Important Product Boundary

Do not turn the Veritas Agent into a second copy of Veritas Core.

Bad architecture:

```
Application
    ↓
Huge intelligent Agent
    ├── PII detection
    ├── DPDPA rules
    ├── evidence
    ├── database
    └── dashboard
```

Preferred architecture:

```
Application
    ↓
Small Agent
    ↓
Veritas Core
    ├── PII detection
    ├── DPDPA rules
    ├── evidence
    ├── database
    └── dashboard
```

This keeps:

- deployment simpler
- upgrades simpler
- compliance logic centralized
- policy management centralized
- security easier to reason about
- enterprise operations easier

## 15. Implementation Priorities

The coding agent should implement the architecture in this order.

### Priority 1: Organisation onboarding

Ensure a fresh Veritas installation behaves as:

```
Blank dashboard
    ↓
Create organisation
    ↓
Organisation becomes active
```

No fake/default customer data.

### Priority 2: Agent registration

Implement/verify:

```
Generate one-time registration key
        ↓
Agent calls /agent/register
        ↓
Server validates key
        ↓
Server creates Agent identity
        ↓
Server returns credentials/configuration
        ↓
Agent stores credentials
```

### Priority 3: Agent telemetry collection

Support at minimum:

- File logs

Then:

- Docker/container logs

Then:

- HTTP/application telemetry where practical.

### Priority 4: Kubernetes

Package the Agent as a Kubernetes DaemonSet.

The DaemonSet should:

- run one Agent per node
- access the configured node/container log sources
- register with Veritas
- forward telemetry
- report health

Do not require changes to every application pod unless a specific telemetry source requires it.

### Priority 5: Non-Kubernetes server

Provide a straightforward server installation/service model.

Example:

```
Linux Server
    ↓
Install Veritas Agent
    ↓
Configure log source
    ↓
Register with Veritas
    ↓
Start Agent service
```

### Priority 6: End-to-end compliance

Verify:

```
Application/log
    ↓
Agent
    ↓
Veritas
    ↓
PII Detection
    ↓
DPDPA Rule
    ↓
Violation
    ↓
Evidence
    ↓
Dashboard
```

### Priority 7: Optional sanitization

Do NOT make this a prerequisite for the core Agent architecture.

Design a configurable downstream action such as:

- raw
- sanitized
- compliance-metadata-only

The exact implementation can be decided after the basic telemetry/compliance pipeline is stable.

## 16. What NOT to Build Yet

Do not expand scope unnecessarily.

Do not build:

- custom operating system
- custom PCB
- Raspberry Pi-specific product architecture
- one Agent implementation per application framework
- universal SIEM replacement
- full observability platform
- compliance logic inside the Agent
- mandatory Kubernetes dependency
- mandatory masking of every event
- complex distributed architecture before measuring current limits

The immediate objective is a credible enterprise-deployable compliance product.

## 17. Final Intended Flow

This is the canonical flow that the implementation should converge toward:

```
                    VERITAS CUSTOMER FLOW

┌──────────────────────────────────────────┐
│          CUSTOMER INFRASTRUCTURE         │
│                                          │
│  Apps / APIs / Containers / Servers      │
│                  │                       │
│                  ▼                       │
│          ┌───────────────┐               │
│          │ Veritas Agent │               │
│          └───────┬───────┘               │
│                  │                       │
└──────────────────┼───────────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │   Veritas Core  │
          │                 │
          │ Ingest          │
          │ PII Detection   │
          │ Policy Engine   │
          │ Violations      │
          │ Evidence        │
          │ Cases           │
          │ Investigation   │
          │ Audit           │
          └────────┬────────┘
                   │
             ┌─────┴─────┐
             ▼           ▼
        Dashboard    Optional
                     Sanitization
                          │
                          ▼
                 Customer Storage /
                 SIEM / Logging
```

The customer-facing lifecycle is:

```
OPEN VERITAS
     ↓
CREATE ORGANISATION
     ↓
CONFIGURE POLICY
     ↓
ISSUE AGENT KEY
     ↓
DEPLOY AGENT
     ↓
AGENT AUTO-REGISTERS
     ↓
AGENT COLLECTS TELEMETRY
     ↓
VERITAS ANALYZES TELEMETRY
     ↓
PII + DPDPA VIOLATION DETECTED
     ↓
EVIDENCE CREATED
     ↓
CUSTOMER SEES VIOLATION
     ↓
CASE / INVESTIGATION / AUDIT
```

This is the architecture to implement unless a later requirement explicitly changes it.
