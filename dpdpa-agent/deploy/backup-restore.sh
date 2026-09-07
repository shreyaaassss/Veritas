#!/usr/bin/env bash
# Veritas Backup & Restore Helper
# =================================
# Convenience wrapper around backup.py for production use.
#
# Usage:
#   bash deploy/backup-restore.sh backup               # create backup
#   bash deploy/backup-restore.sh restore FILENAME.zip # restore (stops service)
#   bash deploy/backup-restore.sh verify  FILENAME.zip # verify integrity
#   bash deploy/backup-restore.sh list                 # list available backups
#
# Scheduled backups (add to crontab as root):
#   0 2 * * * bash /opt/veritas/dpdpa-agent/deploy/backup-restore.sh backup >> /var/log/veritas-backup.log 2>&1

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/veritas}"
PYTHON="${INSTALL_DIR}/venv/bin/python"
BACKUP_PY="${INSTALL_DIR}/dpdpa-agent/backup.py"

# Fall back to system Python for development
if [[ ! -f "$PYTHON" ]]; then
    PYTHON="python3"
fi
if [[ ! -f "$BACKUP_PY" ]]; then
    BACKUP_PY="$(dirname "$0")/../backup.py"
fi

CMD="${1:-}"

case "$CMD" in
  backup)
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting backup..."
    "$PYTHON" "$BACKUP_PY" backup
    ;;

  restore)
    FILE="${2:-}"
    if [[ -z "$FILE" ]]; then
        echo "Usage: $0 restore <backup-filename.zip>" >&2; exit 1
    fi
    echo "WARNING: This will overwrite existing Veritas data."
    echo "         Stop the Veritas service first:"
    echo "           sudo systemctl stop veritas"
    echo ""
    read -rp "Continue? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted."; exit 0
    fi
    "$PYTHON" "$BACKUP_PY" restore "$FILE"
    echo ""
    echo "Restore complete. Start Veritas:"
    echo "  sudo systemctl start veritas"
    ;;

  verify)
    FILE="${2:-}"
    if [[ -z "$FILE" ]]; then
        echo "Usage: $0 verify <backup-filename.zip>" >&2; exit 1
    fi
    "$PYTHON" "$BACKUP_PY" verify "$FILE"
    ;;

  list)
    "$PYTHON" "$BACKUP_PY" list
    ;;

  *)
    echo "Usage: $0 backup | restore <file> | verify <file> | list"
    exit 1
    ;;
esac
