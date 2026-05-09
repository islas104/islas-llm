#!/bin/bash
set -euo pipefail

DB="/Users/ianawaz/Desktop/forge-llm/forge.db"
BACKUP_DIR="/Users/ianawaz/Desktop/forge-llm/data/backups"
DEST="$BACKUP_DIR/forge_$(date +%Y-%m-%d).db"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB" ".backup '$DEST'"

# Keep only the 7 most recent backups
ls -t "$BACKUP_DIR"/forge_*.db | tail -n +8 | xargs rm -f
