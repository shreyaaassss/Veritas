#!/bin/bash
# macOS helpers for .github/workflows/test-macos-agent.yml. Sources the Linux helpers (server,
# violation accounting) and replaces everything that depends on systemd, journald or GNU tools.

source .github/scripts/agent_ci_lib.sh

LOGDIR=/var/log/veritas-ci
AGENT_LOG=/var/log/veritas-agent/agent.log
AGENT_USER=_veritas-agent

start_server() {   # macOS has no setsid
  (cd dpdpa-agent && \
   VERITAS_DATA_DIR=/tmp/vdata VERITAS_AI_MODE=disabled \
   VERITAS_JWT_SECRET=ci-agent-e2e-secret VERITAS_SECURE_COOKIES=true \
   VERITAS_SETUP_CODE=ci-setup-code \
   nohup python run_pipeline.py --port 8000 >> /tmp/server.log 2>&1 &)
}

write_agent_config() {  # $1 = registration key
  sudo tee /etc/veritas-agent/config.yaml >/dev/null <<CFG
veritas_address: ${VERITAS_URL}
registration_key: "$1"
source_label: ci-agent
tls:
  verify: true
sources:
  - type: file
    path: ${LOGDIR}/app.log
    source_system: order-service
  - type: file
    path: ${LOGDIR}/secret.log
    source_system: kyc-service
  - type: file
    path: ${LOGDIR}/later.log
    source_system: payments-service
CFG
  sudo chown "$AGENT_USER":staff /etc/veritas-agent/config.yaml
  sudo chmod 640 /etc/veritas-agent/config.yaml
}

agent_log() { sudo cat "$AGENT_LOG" 2>/dev/null || true; }

agent_log_has() {  # $1 = text
  local out
  out=$(agent_log)
  grep -qF -- "$1" <<<"$out"
}

# launchd: running | stopped | failed | absent
agent_state() {
  local out
  out=$(sudo launchctl print system/com.veritas.agent 2>/dev/null) || { echo absent; return; }
  if grep -q "state = running" <<<"$out"; then echo running; return; fi
  if grep -Eq "last exit code = [1-9]" <<<"$out"; then echo failed; return; fi
  echo stopped
}

wait_service_failed() {
  for _ in $(seq 1 60); do
    [ "$(agent_state)" = failed ] && return 0
    sleep 2
  done
  echo "veritas-agent did not stop"; agent_log | tail -30; return 1
}
