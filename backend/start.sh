#!/bin/sh
set -eu

PORT="${PORT:-8000}"
exec fastapi run app/main.py --host 0.0.0.0 --port "$PORT"
