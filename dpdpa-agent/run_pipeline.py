"""
Veritas DPDPA Compliance Platform — Runtime Entry Point
=========================================================
Starts the Veritas compliance server. Events arrive exclusively from
registered Veritas Agents via POST /v1/{org_id}/events.

No synthetic data generators. No demo mode. Production only.

Usage:
    python run_pipeline.py              # start on port 8000 (HTTPS if cert available)
    python run_pipeline.py --port 8080  # custom port
    VERITAS_TLS=false python run_pipeline.py  # HTTP only (development)
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path

# Load .env (OPENAI_API_KEY, VERITAS_TLS etc.) — no-op if file doesn't exist
try:
    from dotenv import load_dotenv
    _env = Path(__file__).parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    _env = Path(__file__).parent / ".env"
    if _env.exists():
        with open(_env, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Veritas DPDPA Compliance Platform"
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Dashboard port (default: 8000)",
    )
    args = parser.parse_args()

    # ---- License check ----
    from license import LicenseError, validate_license
    try:
        _lic = validate_license()
        print(
            f"[Veritas] License valid — {_lic.org} · {_lic.tier} · "
            f"expires {_lic.expiry} ({_lic.days_remaining}d remaining)"
        )
    except LicenseError as e:
        border = "=" * 60
        print(f"\n{border}\nLICENSE ERROR\n{border}\n{e}\n{border}\n")
        raise SystemExit(1)

    # ---- First-run: copy seed org configs out of the PyInstaller bundle ----
    from runtime_paths import bundle_root, data_root, is_bundled
    if is_bundled():
        import shutil as _shutil
        _src = bundle_root() / "org_config" / "configs"
        _dst = data_root()   / "org_config" / "configs"
        if _src.exists() and not _dst.exists():
            print("[Veritas] First run — copying org configs to data directory...")
            _shutil.copytree(str(_src), str(_dst))

    # ---- Production acceptance check (Phase 32) ----
    try:
        from production_check import print_report, run_checks
        _prod_report = run_checks()
        if not _prod_report["all_passed"]:
            print_report(_prod_report)
            _failed = [r for r in _prod_report["results"] if r["status"] == "fail"]
            if _failed:
                # License failure is already caught above and is fatal.
                # Other failures are logged as warnings — server still starts.
                for r in _failed:
                    if r["check"] != "license":
                        logger.warning("Production check FAILED: %s — %s", r["check"], r["message"])
    except Exception as _e:
        logger.debug("Production check skipped: %s", _e)

    # ---- PII log filter (Phase 9) ----
    try:
        from pii_guard import install_pii_log_filter
        install_pii_log_filter()
    except Exception:
        pass

    # ---- Secrets hardening (Phase 16) ----
    try:
        from secret_manager import harden_all_secrets
        harden_all_secrets()
    except Exception:
        pass

    # ---- Database encryption key init (Phase 17) ----
    try:
        from db_encryption import _load_or_generate_key
        _load_or_generate_key()
    except Exception:
        pass

    # ---- TLS configuration ----
    from tls import cert_fingerprint, get_ssl_params, tls_mode
    ssl_params = get_ssl_params()
    scheme = "https" if ssl_params else "http"

    # When TLS is active, auto-enable secure cookies unless explicitly overridden
    if ssl_params and "VERITAS_SECURE_COOKIES" not in os.environ:
        os.environ["VERITAS_SECURE_COOKIES"] = "true"
    elif not ssl_params and "VERITAS_SECURE_COOKIES" not in os.environ:
        os.environ["VERITAS_SECURE_COOKIES"] = "false"

    # ---- Start the FastAPI server (dashboard + API + WebSocket) ----
    def _run_server() -> None:
        from dashboard.server import app
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=args.port,
            log_level="warning",
            **(ssl_params or {}),
        )

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    dashboard_url = f"{scheme}://localhost:{args.port}"
    logger.info("Dashboard: %s", dashboard_url)

    if ssl_params:
        fp = cert_fingerprint()
        if fp:
            logger.info("TLS certificate fingerprint (SHA-256): %s", fp)
            logger.info(
                "Share the certificate at %s with Veritas Agents "
                "(set VERITAS_CA_CERT on each agent).",
                data_root() / "certs" / "server.crt",
            )
    else:
        logger.warning(
            "Running over HTTP (TLS mode: %s). "
            "Not suitable for production — agent data is unencrypted in transit.",
            tls_mode(),
        )

    # First-boot check: if no users exist, print setup instructions
    try:
        from user_store.store import get_user_store
        if get_user_store().count_users() == 0:
            print()
            print("=" * 60)
            print("  FIRST RUN DETECTED — SETUP REQUIRED")
            print("=" * 60)
            print(f"  Open {dashboard_url}/setup to create")
            print("  your administrator account before logging in.")
            print("=" * 60)
            print()
    except Exception:
        pass

    logger.info("Waiting for agents to connect and forward events...")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
