#!/bin/bash
# Shared shell helpers for .github/workflows/test-agent-install.yml (sourced by each step).
# Expects the repository root as the working directory and VERITAS_* set by the workflow.

E2E="python .github/scripts/agent_e2e.py"
LOGDIR=/var/log/veritas-ci
BASELINE_FILE=/tmp/ci_violation_baseline

# --- server (run from source on this machine) -------------------------------

start_server() {
  (cd dpdpa-agent && \
   VERITAS_DATA_DIR=/tmp/vdata VERITAS_AI_MODE=disabled \
   VERITAS_JWT_SECRET=ci-agent-e2e-secret VERITAS_SECURE_COOKIES=true \
   setsid nohup python run_pipeline.py --port 8000 >> /tmp/server.log 2>&1 &)
}

stop_server() {
  pkill -f "run_pipeline.py --port 8000" || true
  for _ in $(seq 1 30); do
    curl -sk --max-time 2 "$VERITAS_URL/health" >/dev/null 2>&1 || return 0
    sleep 1
  done
  echo "server did not stop"; return 1
}

wait_server() {
  for i in $(seq 1 80); do
    if curl -sk --max-time 3 "$VERITAS_URL/health" 2>/dev/null | grep -q '"status":"ok"'; then
      echo "server is up after about $((i * 3))s"; return 0
    fi
    sleep 3
  done
  echo "server did not become healthy"; tail -40 /tmp/server.log; return 1
}

# --- agent service ----------------------------------------------------------

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
  sudo chown veritas-agent:veritas-agent /etc/veritas-agent/config.yaml
  sudo chmod 640 /etc/veritas-agent/config.yaml
}

wait_service_failed() {
  for _ in $(seq 1 60); do
    systemctl is-failed --quiet veritas-agent && return 0
    sleep 2
  done
  echo "veritas-agent did not stop"; sudo journalctl -u veritas-agent --no-pager | tail -30; return 1
}

agent_log() {
  sudo journalctl -u veritas-agent --no-pager
}

# True if the agent's journal contains the text. Reads the journal first so grep -q
# closing the pipe early cannot make journalctl fail.
agent_log_has() {  # $1 = text
  local out
  out=$(agent_log)
  grep -qF -- "$1" <<<"$out"
}

agent_ids() {  # prints the ids of the agents labelled ci-agent, as a Python list
  $E2E agents > /tmp/ci_agents.json
  python -c "import json; print(sorted(a['agent_id'] for a in json.load(open('/tmp/ci_agents.json')) if a['source_label']=='ci-agent'))"
}

# --- log files the agent reads ----------------------------------------------

append_log() {  # $1 = file name (relative to LOGDIR), $2 = text
  printf '%s\n' "$2" | sudo tee -a "$LOGDIR/$1" >/dev/null
}

# --- exact violation accounting ---------------------------------------------
# Every PII log line used by the test (one phone number or one Aadhaar) yields exactly one
# violation, so after appending N such lines the ledger must hold baseline + N: no more
# (duplicates) and no fewer (lost lines).

set_baseline() {
  $E2E count > "$BASELINE_FILE"
  echo "violation baseline: $(cat "$BASELINE_FILE")"
}

expect_new_violations() {  # $1 = how many new violations are expected in total since the baseline
  local want=$(( $(cat "$BASELINE_FILE") + $1 ))
  $E2E violations --min "$want" --timeout "${2:-90}" >/dev/null
  sleep 6   # give a duplicate a chance to show up
  local got
  got=$($E2E count)
  if [ "$got" != "$want" ]; then
    echo "expected exactly $want violations (baseline + $1), found $got"
    return 1
  fi
  echo "ok: $got violations (baseline + $1)"
}
