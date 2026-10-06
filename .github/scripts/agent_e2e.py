#!/usr/bin/env python3
"""
Helper for the agent install test (.github/workflows/test-agent-install.yml).

Talks to a running Veritas server over its API so the workflow can set the server
up, issue registration keys and assert what the installed agent did. Runs on any
machine with Python 3 and `requests`.

Environment:
  VERITAS_URL             e.g. https://localhost:8000 (self-signed certificate is accepted)
  VERITAS_ADMIN_USER      administrator username      (default: ci_admin)
  VERITAS_ADMIN_PASSWORD  administrator password      (default: CiAdmin#12345)
  VERITAS_ORG             organization id             (default: ci_org)

The session cookie is cached so the login rate limit (10 per 5 minutes) is never hit.

Commands (each exits non-zero on failure):
  setup                       create the admin (first run) and the organization
  issue-key                   print a one-time agent registration key
  wait-agent  --label L [--version V] [--source TARGET=STATE[:DETAIL]]... [--timeout S]
  agents                      print the organization's agents as JSON
  violations  [--min N] [--timeout S] [--source-system S]
                              wait until the ledger holds at least N violations; print the count
  count       [--source-system S]
                              print the current number of violations
  counter     --label L --name NAME [--min N] [--timeout S]
                              wait until the agent's health counter NAME reaches N
  verify-chain                fail unless the evidence chain verifies
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

URL = os.environ.get("VERITAS_URL", "https://localhost:8000").rstrip("/")
USER = os.environ.get("VERITAS_ADMIN_USER", "ci_admin")
PASSWORD = os.environ.get("VERITAS_ADMIN_PASSWORD", "CiAdmin#12345")
ORG = os.environ.get("VERITAS_ORG", "ci_org")
COOKIE_FILE = Path(tempfile.gettempdir()) / "veritas-ci-session.json"

ORG_CONFIG = {
    "org_id": ORG,
    "identifiers": [
        {"name": "aadhaar", "pattern": "indian_aadhaar", "validator": "aadhaar"},
        {"name": "pan", "pattern": "indian_pan", "validator": "pan"},
        {"name": "phone", "pattern": "indian_phone", "validator": "none"},
    ],
    "fields": [
        {"field_name": "name", "pii_category": "name", "declared_purpose": "order_fulfillment",
         "consent_scope": "order_fulfillment", "retention_days": 1095, "source_system": "order-service"},
        {"field_name": "aadhaar", "pii_category": "aadhaar", "declared_purpose": "onboarding_kyc",
         "consent_scope": "onboarding_kyc", "retention_days": 180, "source_system": "kyc-service"},
    ],
}


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.timeout = 30
    if COOKIE_FILE.exists():
        s.cookies.update(json.loads(COOKIE_FILE.read_text()))
        if s.get(f"{URL}/api/auth/me", timeout=30).status_code == 200:
            return s
    r = s.post(f"{URL}/api/auth/login", data={"username": USER, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        fail(f"login failed ({r.status_code}): {r.text[:200]}")
    COOKIE_FILE.write_text(json.dumps(s.cookies.get_dict()))
    COOKIE_FILE.chmod(0o600)
    return s


def cmd_setup(_: argparse.Namespace) -> None:
    s = requests.Session()
    s.verify = False
    r = s.post(f"{URL}/api/auth/setup", json={"username": USER, "email": "ci@example.com",
                                                "password": PASSWORD}, timeout=30)
    if r.status_code not in (200, 403):  # 403 = setup already done
        fail(f"setup failed ({r.status_code}): {r.text[:200]}")
    c = session()
    r = c.post(f"{URL}/v1/orgs/{ORG}/config", json=ORG_CONFIG, timeout=30)
    if r.status_code != 200 or r.json().get("status") != "ok":
        fail(f"org config rejected: {r.text[:300]}")
    print(f"ready: admin '{USER}', organization '{ORG}'")


def cmd_issue_key(_: argparse.Namespace) -> None:
    r = session().post(f"{URL}/agents/issue-key", json={"org_id": ORG}, timeout=30)
    if r.status_code != 200:
        fail(f"issue-key failed ({r.status_code}): {r.text[:200]}")
    print(r.json()["key"])


def agents() -> list:
    r = session().get(f"{URL}/agents", params={"org_id": ORG}, timeout=30)
    if r.status_code != 200:
        fail(f"listing agents failed ({r.status_code}): {r.text[:200]}")
    return r.json()["agents"]


def find_agent(label: str):
    matches = [a for a in agents() if a["source_label"] == label]
    return matches[-1] if matches else None


def poll(check, timeout: float, what: str, interval: float = 3.0):
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            ok, last = check()
        except requests.RequestException as e:
            ok, last = False, f"request error: {e}"
        if ok:
            return last
        if time.monotonic() >= deadline:
            fail(f"timed out after {int(timeout)}s waiting for {what}. Last state: {last}")
        time.sleep(interval)


def cmd_wait_agent(a: argparse.Namespace) -> None:
    wanted = []
    for spec in a.source or []:
        target, _, state = spec.partition("=")
        state, _, detail = state.partition(":")
        wanted.append((target, state, detail))

    def check():
        agent = find_agent(a.label)
        if agent is None:
            return False, "agent not registered yet"
        if agent["status"] != "ACTIVE":
            return False, f"status {agent['status']}"
        health = agent.get("health")
        if not health:
            return False, "no health report yet"
        if a.version and agent.get("agent_version") != a.version:
            return False, f"version {agent.get('agent_version')!r}, want {a.version!r}"
        sources = health.get("sources", [])
        for target, state, detail in wanted:
            hit = [s for s in sources if target in s["target"]]
            if not hit:
                return False, f"source {target!r} not reported (have {[s['target'] for s in sources]})"
            if hit[0]["state"] != state or (detail and detail not in hit[0]["detail"]):
                return False, f"source {target!r} is {hit[0]['state']} {hit[0]['detail']!r}"
        return True, agent

    agent = poll(check, a.timeout, f"agent {a.label!r} to be healthy")
    print(json.dumps({"agent_id": agent["agent_id"], "version": agent["agent_version"],
                      "sources": [(s["target"].split("/")[-1], s["state"], s["detail"])
                                  for s in agent["health"]["sources"]]}))


def count_violations(source_system: str | None = None) -> int:
    params = {"source_system": source_system} if source_system else {}
    r = session().get(f"{URL}/api/{ORG}/verdicts", params=params, timeout=30)
    if r.status_code != 200:
        fail(f"listing verdicts failed ({r.status_code}): {r.text[:200]}")
    return r.json()["count"]


def cmd_violations(a: argparse.Namespace) -> None:
    n = poll(lambda: ((c := count_violations(a.source_system)) >= a.min, c),
             a.timeout, f"{a.min} violation(s)")
    print(n)


def cmd_count(a: argparse.Namespace) -> None:
    print(count_violations(a.source_system))


def cmd_counter(a: argparse.Namespace) -> None:
    def check():
        agent = find_agent(a.label)
        value = ((agent or {}).get("health") or {}).get("counters", {}).get(a.name, 0)
        return value >= a.min, value

    print(poll(check, a.timeout, f"counter {a.name} >= {a.min} on agent {a.label!r}"))


def cmd_verify_chain(_: argparse.Namespace) -> None:
    r = session().get(f"{URL}/api/{ORG}/verify-chain", timeout=30)
    if r.status_code != 200 or not r.json().get("valid"):
        fail(f"evidence chain did not verify: {r.text[:300]}")
    print(f"chain valid, {r.json().get('total_records')} records")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("issue-key").set_defaults(fn=cmd_issue_key)
    sub.add_parser("agents").set_defaults(fn=lambda a: print(json.dumps(agents(), indent=2)))
    sub.add_parser("verify-chain").set_defaults(fn=cmd_verify_chain)

    w = sub.add_parser("wait-agent")
    w.add_argument("--label", required=True)
    w.add_argument("--version")
    w.add_argument("--source", action="append", help="TARGET=STATE[:DETAIL]")
    w.add_argument("--timeout", type=float, default=120)
    w.set_defaults(fn=cmd_wait_agent)

    v = sub.add_parser("violations")
    v.add_argument("--min", type=int, default=1)
    v.add_argument("--timeout", type=float, default=90)
    v.add_argument("--source-system")
    v.set_defaults(fn=cmd_violations)

    c = sub.add_parser("count")
    c.add_argument("--source-system")
    c.set_defaults(fn=cmd_count)

    k = sub.add_parser("counter")
    k.add_argument("--label", required=True)
    k.add_argument("--name", required=True)
    k.add_argument("--min", type=int, default=1)
    k.add_argument("--timeout", type=float, default=90)
    k.set_defaults(fn=cmd_counter)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
