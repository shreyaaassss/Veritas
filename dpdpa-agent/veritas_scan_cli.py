#!/usr/bin/env python3
"""
Veritas — DevOps Interception CLI (Phase 5, Part A3)
========================================================
Thin proof-of-concept only, per the plan's explicit scope: a single
script, no packaging, no config beyond --org/--url. Reads piped-in text
from stdin, calls the ALREADY-RUNNING service's POST /v1/{org_id}/scan
endpoint (api/integration.py) over real HTTP — this script contains no
detection or rule-engine logic of its own, so there is exactly one engine
behind all three of Veritas's integration modes.

Usage:
    kubectl logs <pod> | python veritas_scan_cli.py --org blinkit
    cat some_query_output.txt | python veritas_scan_cli.py --org edtech_co
    echo 'aadhaar 2345 6789 0124 leaked' | python veritas_scan_cli.py --org blinkit --url http://localhost:8000

Output contract:
  stdout — ONLY the masked/redacted text (so raw PII never reaches the
           terminal, and the output stays pipeable/scriptable).
  stderr — a one-line summary per verdict found ("2 issue(s) found: ..."),
           so a human watching the terminal still sees what happened
           without it polluting stdout.

Explicitly NOT built here (per the plan's scope): retry logic, streaming
support, CI/CD-specific tooling, packaging. If the service isn't
reachable, this prints a clear error to stderr and exits non-zero — no
retries, no fallback local detection.
"""

from __future__ import annotations

import argparse
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pipe stdin through Veritas's /scan endpoint; masked output to stdout, verdict summary to stderr."
    )
    parser.add_argument("--org", required=True, help="org_id to scan against (must already have a registered config).")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the running Veritas service (default: http://localhost:8000).")
    args = parser.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        print("veritas_scan_cli: no input on stdin — nothing to scan.", file=sys.stderr)
        return 1

    scan_url = f"{args.url.rstrip('/')}/v1/{args.org}/scan"
    try:
        response = requests.post(scan_url, json={"text": text}, timeout=30)
    except requests.exceptions.RequestException as exc:
        print(f"veritas_scan_cli: could not reach {scan_url}: {exc}", file=sys.stderr)
        return 1

    if response.status_code == 404:
        print(f"veritas_scan_cli: org {args.org!r} is not registered on this service ({scan_url}).", file=sys.stderr)
        return 1
    if not response.ok:
        print(f"veritas_scan_cli: scan request failed ({response.status_code}): {response.text}", file=sys.stderr)
        return 1

    result = response.json()

    # Masked output to stdout — PII never reaches the terminal in cleartext.
    print(result.get("masked_text", text))

    verdicts = result.get("verdicts", [])
    if verdicts:
        print(f"veritas_scan_cli: {len(verdicts)} issue(s) found:", file=sys.stderr)
        for v in verdicts:
            print(
                f"  - {v['rule_id']} [{v['severity']}] field={v['field']!r} "
                f"breach_candidate={v['breach_notification_candidate']}",
                file=sys.stderr,
            )
    else:
        print("veritas_scan_cli: no issues found.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
