#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
DATABASE_URL="${DATABASE_URL:-}"

mkdir -p "$BACKUP_DIR"

if [[ -z "$DATABASE_URL" ]]; then
    echo "ERRO: DATABASE_URL não configurada." >&2
    exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/frcaixinha_${TIMESTAMP}.dump"

pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_FILE"

echo "Backup criado: $BACKUP_FILE"
