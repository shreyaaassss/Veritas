"""
Veritas DPDPA Compliance Platform — Runtime Entry Point
=========================================================
Starts the Veritas compliance server. Events arrive exclusively from
registered Veritas Agents via POST /v1/{org_id}/events.

No synthetic data generators. No demo mode. Production only.

Usage:
    python run_pipeline.py              # start on port 8000
    python run_pipeline.py --port 8080  # custom port
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path

# Load .env (OPENAI_API_KEY etc.) — no-op if file doesn't exist
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

    # ---- Start the FastAPI server (dashboard + API + WebSocket) ----
    def _run_server() -> None:
        from dashboard.server import app
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    logger.info("Dashboard available at http://localhost:%d", args.port)
    logger.info("Waiting for agents to connect and forward events...")

    # Keep the process alive — the server runs in the daemon thread.
    # Events arrive via POST /v1/{org_id}/events from Veritas Agents.
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
