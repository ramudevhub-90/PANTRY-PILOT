#!/usr/bin/env bash
# Package the Pantry Pilot server for AWS Lambda (Python 3.13, arm64) into infra/build/lambda.
# The Lambda Web Adapter layer runs run.sh, which starts the same Starlette app we run locally.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/infra/build/lambda"
PIP="${PIP:-$ROOT/server/.venv/bin/pip}"

rm -rf "$OUT"
mkdir -p "$OUT"

# boto3 ships with the Lambda Python runtime, so it isn't bundled.
"$PIP" install --quiet --target "$OUT" \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.13 --only-binary=:all: \
  "mcp==2.3.0" "starlette>=1.7" "uvicorn>=0.54"

cp -R "$ROOT/server/pantry_pilot" "$OUT/"
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +

cat > "$OUT/run.sh" <<'EOF'
#!/bin/bash
exec python3 -m uvicorn --factory pantry_pilot.app:create_app --host 0.0.0.0 --port "${PORT:-8080}" --no-access-log
EOF
chmod +x "$OUT/run.sh"

echo "Lambda bundle: $(du -sh "$OUT" | cut -f1) in $OUT"
