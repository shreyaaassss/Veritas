"""
Veritas Backup & Recovery
==========================
Creates and restores complete, verified backups of all Veritas data.

What is backed up:
  - evidence.db        — compliance verdicts (tamper-evident ledger)
  - agents.db          — agent registrations and tokens
  - users.db           — user accounts and org memberships
  - audit.db           — system audit log
  - org_config/        — organisation compliance policies (YAML)
  - veritas.vlic       — license file
  - certs/             — TLS certificate and key
  - secrets/           — JWT signing key

What is NOT backed up:
  - pipeline.log       — transient logs, not required for recovery
  - .veritas_state.json — agent state, re-established on re-registration

Backup format: ZIP archive with a manifest.json listing all files and their
SHA-256 checksums for integrity verification.

Usage (CLI):
  python backup.py backup [--out /path/to/backup.zip]
  python backup.py restore /path/to/backup.zip [--dry-run]
  python backup.py verify /path/to/backup.zip

API:
  from backup import create_backup, restore_backup, verify_backup
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("veritas.backup")

_BACKUP_VERSION = "1"
_MANIFEST_FILE  = "backup_manifest.json"

# Files/dirs to include (relative to data_root())
_BACKUP_TARGETS = [
    "evidence.db",
    "agents.db",
    "users.db",
    "audit.db",
    "veritas.vlic",
    "org_config/configs",
    "certs",
    "secrets",
]

# Files that are critical for restore to succeed
_CRITICAL_FILES = ["evidence.db", "agents.db", "users.db"]


def _data_root() -> Path:
    from runtime_paths import data_root
    return data_root()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_backup(output_path: Optional[Path] = None) -> Path:
    """
    Create a complete backup ZIP of all Veritas data.

    Returns the path to the created backup file.

    The backup is verified immediately after creation — if the manifest
    checksums don't match the written data, an error is raised before
    the backup is considered complete.
    """
    root = _data_root()
    now  = datetime.now(timezone.utc)

    if output_path is None:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        output_path = root / "backups" / f"veritas-backup-{ts}.zip"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest: Dict = {
        "backup_version": _BACKUP_VERSION,
        "created_at":     now.isoformat(),
        "data_root":      str(root),
        "files":          {},
    }

    logger.info("Creating backup at %s ...", output_path)
    files_added = 0

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for target in _BACKUP_TARGETS:
            src = root / target
            if not src.exists():
                logger.debug("Skipping (not found): %s", src)
                continue

            if src.is_file():
                arcname = target
                zf.write(src, arcname)
                manifest["files"][arcname] = {
                    "type":   "file",
                    "sha256": _sha256_file(src),
                    "size":   src.stat().st_size,
                }
                files_added += 1

            elif src.is_dir():
                for child in sorted(src.rglob("*")):
                    if child.is_file():
                        # Use forward slashes so manifest keys match zipfile's namelist
                        # (zipfile normalises to forward slashes on all platforms)
                        arcname = child.relative_to(root).as_posix()
                        zf.write(child, arcname)
                        manifest["files"][arcname] = {
                            "type":   "file",
                            "sha256": _sha256_file(child),
                            "size":   child.stat().st_size,
                        }
                        files_added += 1

        # Write manifest last
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        zf.writestr(_MANIFEST_FILE, manifest_bytes)

    # Immediate verification
    ok, errors = verify_backup(output_path)
    if not ok:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup verification failed after creation: {errors}")

    size_mb = output_path.stat().st_size / 1_048_576
    logger.info(
        "Backup created: %s (%.1f MB, %d files, verified OK)",
        output_path, size_mb, files_added,
    )
    return output_path


def verify_backup(backup_path: Path) -> tuple[bool, List[str]]:
    """
    Verify a backup archive by checking all file SHA-256 checksums
    against the manifest.

    Returns (True, []) if all checksums match.
    Returns (False, [list of errors]) if any file is missing or corrupted.
    """
    errors: List[str] = []

    if not backup_path.exists():
        return False, [f"Backup file not found: {backup_path}"]

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            # Read manifest
            if _MANIFEST_FILE not in zf.namelist():
                return False, ["Manifest file missing from backup."]

            manifest = json.loads(zf.read(_MANIFEST_FILE))

            # Check each file
            for arcname, info in manifest.get("files", {}).items():
                if arcname not in zf.namelist():
                    errors.append(f"Missing file in archive: {arcname}")
                    continue

                data = zf.read(arcname)
                actual_sha256 = _sha256_bytes(data)
                if actual_sha256 != info["sha256"]:
                    errors.append(f"Checksum mismatch: {arcname}")
                    continue

                if len(data) != info.get("size", len(data)):
                    errors.append(f"Size mismatch: {arcname}")

    except zipfile.BadZipFile:
        return False, ["Backup file is corrupt (not a valid ZIP)."]
    except Exception as e:
        return False, [f"Verification error: {e}"]

    return len(errors) == 0, errors


def restore_backup(backup_path: Path, *, dry_run: bool = False,
                   target_root: Optional[Path] = None) -> dict:
    """
    Restore Veritas data from a backup archive.

    IMPORTANT: Stop the Veritas server before restoring.

    Args:
        backup_path:  Path to the backup ZIP file.
        dry_run:      If True, validate the backup but don't write any files.
        target_root:  Override data directory (default: current data_root()).

    Returns a dict with: restored_files, skipped_files, errors, success.
    """
    result = {"restored_files": [], "skipped_files": [], "errors": [], "success": False}

    # Verify first
    ok, verify_errors = verify_backup(backup_path)
    if not ok:
        result["errors"] = verify_errors
        logger.error("Restore aborted: backup verification failed — %s", verify_errors)
        return result

    root = target_root or _data_root()

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            manifest = json.loads(zf.read(_MANIFEST_FILE))

            for arcname in manifest.get("files", {}):
                dest = root / arcname

                if dry_run:
                    result["restored_files"].append(arcname)
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(dest, "wb") as f:
                        f.write(zf.read(arcname))
                    result["restored_files"].append(arcname)

                    # Secure permissions for sensitive files
                    if "secrets" in arcname or "certs/server.key" in arcname:
                        try:
                            dest.chmod(0o600)
                        except Exception:
                            pass
                except Exception as e:
                    result["errors"].append(f"Could not restore {arcname}: {e}")

    except Exception as e:
        result["errors"].append(f"Restore error: {e}")
        return result

    # Check critical files landed correctly
    if not dry_run:
        for critical in _CRITICAL_FILES:
            if not (root / critical).exists():
                result["errors"].append(f"Critical file missing after restore: {critical}")

    result["success"] = len(result["errors"]) == 0
    action = "DRY RUN" if dry_run else "RESTORE"
    logger.info(
        "%s complete: %d files restored, %d errors",
        action, len(result["restored_files"]), len(result["errors"]),
    )
    return result


def list_backups(backup_dir: Optional[Path] = None) -> List[dict]:
    """List available backup files with metadata."""
    if backup_dir is None:
        backup_dir = _data_root() / "backups"

    if not backup_dir.exists():
        return []

    backups = []
    for f in sorted(backup_dir.glob("veritas-backup-*.zip"), reverse=True):
        try:
            with zipfile.ZipFile(f, "r") as zf:
                if _MANIFEST_FILE in zf.namelist():
                    manifest = json.loads(zf.read(_MANIFEST_FILE))
                    backups.append({
                        "filename":   f.name,
                        "path":       str(f),
                        "created_at": manifest.get("created_at"),
                        "file_count": len(manifest.get("files", {})),
                        "size_bytes": f.stat().st_size,
                    })
        except Exception:
            backups.append({
                "filename": f.name, "path": str(f),
                "created_at": None, "file_count": None,
                "size_bytes": f.stat().st_size,
            })

    return backups


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def _cli():
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Veritas Backup & Recovery")
    sub = parser.add_subparsers(dest="command", required=True)

    # backup
    bp = sub.add_parser("backup", help="Create a backup")
    bp.add_argument("--out", type=Path, help="Output path (default: data_root/backups/)")

    # restore
    rp = sub.add_parser("restore", help="Restore from backup")
    rp.add_argument("path", type=Path, help="Path to backup ZIP")
    rp.add_argument("--dry-run", action="store_true", help="Validate without writing")

    # verify
    vp = sub.add_parser("verify", help="Verify backup integrity")
    vp.add_argument("path", type=Path, help="Path to backup ZIP")

    # list
    sub.add_parser("list", help="List available backups")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.command == "backup":
        try:
            out = create_backup(args.out)
            print(f"Backup created: {out}")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)

    elif args.command == "restore":
        print("WARNING: Restore will overwrite existing data. Stop Veritas first.")
        result = restore_backup(args.path, dry_run=args.dry_run)
        for f in result["restored_files"]:
            print(f"  {'[DRY RUN] ' if args.dry_run else ''}restored: {f}")
        for e in result["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
        print(f"\n{'DRY RUN ' if args.dry_run else ''}{'OK' if result['success'] else 'FAILED'}: "
              f"{len(result['restored_files'])} files")
        sys.exit(0 if result["success"] else 1)

    elif args.command == "verify":
        ok, errors = verify_backup(args.path)
        if ok:
            print(f"VERIFIED OK: {args.path}")
        else:
            for e in errors:
                print(f"  ERROR: {e}", file=sys.stderr)
            print(f"VERIFICATION FAILED: {args.path}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        backups = list_backups()
        if not backups:
            print("No backups found.")
        for b in backups:
            size_mb = b["size_bytes"] / 1_048_576
            print(f"  {b['filename']}  {b['created_at'] or 'unknown'}  "
                  f"{b.get('file_count', '?')} files  {size_mb:.1f} MB")


if __name__ == "__main__":
    _cli()
