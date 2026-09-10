#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "FRcaixinha Release Candidate v0.23"
echo "Root: $ROOT"

if [[ -f "backend/alembic.ini" ]]; then
    cd backend
    alembic heads
fi

echo "RC v0.23: verificação concluída."
