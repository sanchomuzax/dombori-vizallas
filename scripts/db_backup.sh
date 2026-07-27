#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
ENV_FILE="$SCRIPT_DIR/../.env.local"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env.local not found at $ENV_FILE" >&2
  exit 1
fi

source "$ENV_FILE"

BACKUP_DIR="${DOMBORI_BACKUP_DIR:-$HOME/backups/dombori}"
mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/dombori-$(date +%Y%m%d-%H%M%S).dump"

docker exec dombori_db pg_dump -U "$DOMBORI_DB_USER" -Fc "$DOMBORI_DB_NAME" > "$BACKUP_FILE"

find "$BACKUP_DIR" -name "dombori-*.dump" -mtime +14 -delete

echo "Backup created: $BACKUP_FILE"
