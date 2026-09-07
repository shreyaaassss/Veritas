"""
Veritas Agent — Deployable telemetry forwarder
=========================================================
Reads log files and Docker container logs, forwards every line to the
Veritas Server as an authenticated event.

Deliberately dumb by design — no PII detection, no compliance logic,
no authoritative storage. Its only job: read → authenticate → forward.

TLS support:
  The agent communicates with the Veritas Server over HTTPS by default
  when the server address starts with https://.

  TLS verification is controlled by:
    - VERITAS_CA_CERT       path to server's CA/self-signed cert (recommended)
    - VERITAS_TLS_VERIFY    "true" (default) | "false" (insecure, dev only)

  The server certificate can be obtained from:
    GET {veritas_address}/api/tls/cert

Usage:
    python agent.py [--config agent-config.yaml]

Config file (agent-config.yaml):
    veritas_address: https://192.168.1.50:8000
    registration_key: <one-time-key>
    source_label: production-server-1

    tls:
      ca_cert: /etc/veritas/server.crt   # path to server cert for verification
      verify: true                        # set false only for development

    sources:
      - type: file
        path: /var/log/order-service/app.log
        source_system: order-service
      - type: docker
        container: marketing-service
        source_system: marketing-analytics
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

import requests
import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("veritas.agent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE              = Path(".veritas_state.json")
CONFIG_FILE_DEFAULT     = Path("agent-config.yaml")
HEARTBEAT_INTERVAL      = 30    # seconds
BUFFER_MAX              = 1000  # max queued events before dropping
FORWARD_TIMEOUT         = 10    # seconds per HTTP request
REGISTRATION_RETRIES    = 5
REGISTRATION_RETRY_DELAY = 5   # seconds between registration attempts
MAX_BACKOFF             = 30    # seconds, forwarding retry cap

# ---------------------------------------------------------------------------
# TLS configuration
# ---------------------------------------------------------------------------

def _tls_verify(config: Dict[str, Any]) -> Union[bool, str]:
    """
    Return the TLS verification setting for requests calls.

    Priority (highest to lowest):
      1. VERITAS_TLS_VERIFY env var ("false" → skip, "true" or missing → verify)
      2. VERITAS_CA_CERT env var → path to CA/server cert
      3. config["tls"]["ca_cert"] → path to CA/server cert
      4. config["tls"]["verify"] == False → skip (dev only)
      5. Default: True (strict verification)

    Returns:
      True      — use system CA bundle (requires cert signed by trusted CA)
      str       — path to CA cert file (for self-signed / private CA)
      False     — skip TLS verification (INSECURE — log warning)
    """
    # Environment overrides take highest priority
    env_verify = os.environ.get("VERITAS_TLS_VERIFY", "").strip().lower()
    if env_verify == "false":
        logger.warning(
            "TLS verification disabled (VERITAS_TLS_VERIFY=false). "
            "This is insecure and should not be used in production."
        )
        return False

    env_ca = os.environ.get("VERITAS_CA_CERT", "").strip()
    if env_ca:
        if Path(env_ca).exists():
            return env_ca
        logger.warning("VERITAS_CA_CERT path does not exist: %s — falling back to system CA", env_ca)
        return True

    # Config file TLS section
    tls_cfg = config.get("tls", {}) or {}
    cfg_ca = tls_cfg.get("ca_cert", "")
    if cfg_ca and Path(cfg_ca).exists():
        return str(cfg_ca)

    cfg_verify = tls_cfg.get("verify", True)
    if cfg_verify is False:
        logger.warning(
            "TLS verification disabled in config (tls.verify: false). "
            "This is insecure and should not be used in production."
        )
        return False

    return True  # default: strict verification


def _fetch_server_cert(base_url: str, dest: Path, verify: Union[bool, str]) -> bool:
    """
    Download the server's TLS certificate from GET {base_url}/api/tls/cert.
    Saves it to dest. Returns True on success.
    Used during enrollment to obtain the CA cert for future connections.
    """
    try:
        url = base_url.rstrip("/") + "/api/tls/cert"
        resp = requests.get(url, timeout=10, verify=verify)
        if resp.ok and resp.text.startswith("-----BEGIN CERTIFICATE"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(resp.text, encoding="utf-8")
            logger.info("Downloaded server TLS certificate to %s", dest)
            return True
    except Exception as e:
        logger.debug("Could not fetch server cert: %s", e)
    return False


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        logger.error("Config file not found: %s", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg.get("veritas_address"):
        logger.error("Config missing required field: veritas_address")
        sys.exit(1)
    return cfg


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Could not read state file: %s — starting fresh", e)
    return {}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def state_is_valid(state: Dict[str, Any]) -> bool:
    return bool(state.get("agent_id") and state.get("auth_token") and state.get("event_endpoint"))


# ---------------------------------------------------------------------------
# Bootstrap registration
# ---------------------------------------------------------------------------

def bootstrap(config: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    if state_is_valid(state):
        logger.info("Using existing identity: %s (org: %s)", state["agent_id"], state.get("org_id"))
        return state

    if not config.get("registration_key"):
        logger.error(
            "No registration_key in config and no saved identity. "
            "Issue a key from the Veritas dashboard and add it to agent-config.yaml."
        )
        sys.exit(1)

    base = config["veritas_address"].rstrip("/")
    url  = f"{base}/agent/register"
    payload = {
        "registration_key": config["registration_key"],
        "source_label": config.get("source_label", ""),
    }

    # Determine TLS verification for registration calls.
    # On first contact with a self-signed server, we may need to download the cert first.
    verify = _tls_verify(config)
    is_https = base.startswith("https://")

    # If HTTPS + strict verify but no CA cert configured → try to fetch server cert first
    if is_https and verify is True:
        default_ca_path = Path(".veritas_server.crt")
        if not default_ca_path.exists():
            logger.info(
                "HTTPS server detected but no CA cert configured. "
                "Attempting to fetch server certificate from %s/api/tls/cert ...", base
            )
            # First attempt with no verification to get the cert (bootstrap trust)
            if _fetch_server_cert(base, default_ca_path, verify=False):
                verify = str(default_ca_path)
            else:
                logger.warning(
                    "Could not fetch server cert. Proceeding with system CA bundle. "
                    "If this fails, set VERITAS_CA_CERT to the server's certificate path."
                )
        else:
            verify = str(default_ca_path)

    for attempt in range(1, REGISTRATION_RETRIES + 1):
        try:
            logger.info("Registering with server at %s (attempt %d/%d)…", base, attempt, REGISTRATION_RETRIES)
            resp = requests.post(url, json=payload, timeout=FORWARD_TIMEOUT, verify=verify)
            resp.raise_for_status()
            data = resp.json()

            state.update({
                "agent_id":       data["agent_id"],
                "auth_token":     data["auth_token"],
                "org_id":         data["org_id"],
                "event_endpoint": data["event_endpoint"],
                "tls_verify":     str(verify),   # persist for future calls
            })
            save_state(state)

            logger.info(
                "Registered as %s | org: %s | endpoint: %s | TLS: %s",
                state["agent_id"], state["org_id"], state["event_endpoint"],
                "HTTPS" if is_https else "HTTP",
            )
            return state

        except requests.exceptions.SSLError as e:
            logger.error(
                "TLS certificate verification failed: %s\n"
                "Solutions:\n"
                "  1. Set VERITAS_CA_CERT to the server's certificate path.\n"
                "  2. Set VERITAS_TLS_VERIFY=false (insecure, dev only).\n"
                "  3. Download cert: GET %s/api/tls/cert",
                e, base,
            )
            sys.exit(1)

        except requests.exceptions.ConnectionError:
            logger.warning("Server unreachable. Retrying in %ds…", REGISTRATION_RETRY_DELAY)
        except requests.exceptions.HTTPError as e:
            logger.error("Registration rejected (%s): %s", e.response.status_code, e.response.text)
            sys.exit(1)
        except Exception as e:
            logger.warning("Registration attempt failed: %s. Retrying…", e)

        if attempt < REGISTRATION_RETRIES:
            time.sleep(REGISTRATION_RETRY_DELAY)

    logger.error("Could not reach the server after %d attempts. Exiting.", REGISTRATION_RETRIES)
    sys.exit(1)


# ---------------------------------------------------------------------------
# TLS verify for ongoing calls (from persisted state)
# ---------------------------------------------------------------------------

def _verify_from_state(config: Dict[str, Any], state: Dict[str, Any]) -> Union[bool, str]:
    """
    Return the TLS verify setting for post-registration calls.
    Uses the persisted state value if available, falling back to config.
    """
    persisted = state.get("tls_verify", "")
    if persisted == "False" or persisted is False:
        return False
    if persisted and persisted not in ("True", "true", ""):
        p = Path(persisted)
        if p.exists():
            return str(p)
    return _tls_verify(config)


# ---------------------------------------------------------------------------
# Event forwarding
# ---------------------------------------------------------------------------

def _build_headers(state: Dict[str, Any]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {state['auth_token']}",
        "Content-Type": "application/json",
    }


def forward_event(
    base_url: str,
    state: Dict[str, Any],
    raw_snippet: str,
    source_system: str,
    verify: Union[bool, str] = True,
) -> None:
    url     = base_url.rstrip("/") + state["event_endpoint"]
    headers = _build_headers(state)
    payload = {
        "source_type":   "LOG",
        "source_system": source_system,
        "raw_snippet":   raw_snippet,
        "fields":        {},
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=FORWARD_TIMEOUT, verify=verify)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Forwarding worker (runs on main thread)
# ---------------------------------------------------------------------------

def forwarding_worker(
    config: Dict[str, Any],
    state: Dict[str, Any],
    event_queue: queue.Queue,
) -> None:
    base_url = config["veritas_address"]
    verify   = _verify_from_state(config, state)
    backoff  = 1

    logger.info("Forwarding worker started. Watching queue…")

    while True:
        try:
            raw_snippet, source_system = event_queue.get(timeout=1)
        except queue.Empty:
            continue

        while True:
            try:
                forward_event(base_url, state, raw_snippet, source_system, verify=verify)
                backoff = 1
                break

            except requests.exceptions.SSLError as e:
                logger.error(
                    "TLS error forwarding event: %s — check VERITAS_CA_CERT. Dropping event.", e
                )
                break

            except requests.exceptions.ConnectionError as e:
                logger.warning("Server unreachable, retrying in %ds: %s", backoff, e)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status in (401, 403):
                    logger.error(
                        "Auth rejected (%s) — agent may be revoked. Dropping event. "
                        "Re-register a new agent from the dashboard.",
                        status,
                    )
                    break
                logger.warning("HTTP %s from server, retrying in %ds", status, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

            except Exception as e:
                logger.error("Unexpected forwarding error: %s — dropping event", e)
                break


# ---------------------------------------------------------------------------
# Source: file tail
# ---------------------------------------------------------------------------

def tail_file(
    path: str,
    source_system: str,
    event_queue: queue.Queue,
) -> None:
    file_path = Path(path)
    logger.info("Tailing file: %s (source_system: %s)", file_path, source_system)

    while not file_path.exists():
        logger.warning("Waiting for log file to appear: %s", file_path)
        time.sleep(5)

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        _enqueue(event_queue, line, source_system)
                else:
                    time.sleep(0.5)
    except Exception as e:
        logger.error("File tail error for %s: %s", path, e)


# ---------------------------------------------------------------------------
# Source: Docker container log tail
# ---------------------------------------------------------------------------

def tail_docker(
    container_name: str,
    source_system: str,
    event_queue: queue.Queue,
) -> None:
    logger.info("Tailing Docker container: %s (source_system: %s)", container_name, source_system)
    try:
        import docker  # type: ignore
    except ImportError:
        logger.error(
            "docker package not installed. Run: pip install docker>=7.0.0 — "
            "skipping container source '%s'", container_name
        )
        return

    try:
        client    = docker.from_env()
        container = client.containers.get(container_name)
    except Exception as e:
        logger.error("Cannot connect to Docker or find container '%s': %s — skipping", container_name, e)
        return

    try:
        for log_bytes in container.logs(stream=True, follow=True, tail=0):
            line = log_bytes.decode("utf-8", errors="replace").strip()
            if line:
                _enqueue(event_queue, line, source_system)
    except Exception as e:
        logger.error("Docker log stream error for '%s': %s", container_name, e)


def _enqueue(event_queue: queue.Queue, line: str, source_system: str) -> None:
    try:
        event_queue.put_nowait((line, source_system))
    except queue.Full:
        try:
            event_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            event_queue.put_nowait((line, source_system))
        except queue.Full:
            logger.warning("Event buffer full — dropped line from %s", source_system)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def heartbeat_loop(
    config: Dict[str, Any],
    state: Dict[str, Any],
    interval: int = HEARTBEAT_INTERVAL,
) -> None:
    base_url = config["veritas_address"].rstrip("/")
    url      = f"{base_url}/agent/heartbeat"
    headers  = _build_headers(state)
    verify   = _verify_from_state(config, state)

    logger.info("Heartbeat thread started (every %ds)", interval)
    while True:
        time.sleep(interval)
        try:
            resp = requests.post(
                url,
                json={"agent_id": state["agent_id"]},
                headers=headers,
                timeout=5,
                verify=verify,
            )
            if resp.ok:
                logger.debug("Heartbeat sent OK")
            else:
                logger.warning("Heartbeat returned %s", resp.status_code)
        except Exception as e:
            logger.warning("Heartbeat failed (will retry next interval): %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Veritas Agent — telemetry forwarder")
    parser.add_argument(
        "--config", type=Path, default=CONFIG_FILE_DEFAULT,
        help=f"Path to agent config YAML (default: {CONFIG_FILE_DEFAULT})",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    state  = load_state()
    state  = bootstrap(config, state)

    sources = config.get("sources", [])
    if not sources:
        logger.warning("No sources configured in agent-config.yaml — agent will only send heartbeats")

    event_queue: queue.Queue = queue.Queue(maxsize=BUFFER_MAX)

    for source in sources:
        src_type   = source.get("type", "").lower()
        src_system = source.get("source_system", "unknown")

        if src_type == "file":
            t = threading.Thread(
                target=tail_file,
                args=(source["path"], src_system, event_queue),
                daemon=True,
                name=f"tail-file-{src_system}",
            )
            t.start()

        elif src_type == "docker":
            t = threading.Thread(
                target=tail_docker,
                args=(source["container"], src_system, event_queue),
                daemon=True,
                name=f"tail-docker-{source['container']}",
            )
            t.start()

        else:
            logger.warning("Unknown source type %r — skipping", src_type)

    hb = threading.Thread(
        target=heartbeat_loop,
        args=(config, state),
        daemon=True,
        name="heartbeat",
    )
    hb.start()

    logger.info(
        "Agent %s running. Forwarding to %s%s",
        state["agent_id"], config["veritas_address"], state["event_endpoint"],
    )

    forwarding_worker(config, state, event_queue)


if __name__ == "__main__":
    main()
