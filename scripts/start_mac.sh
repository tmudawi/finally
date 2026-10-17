#!/usr/bin/env bash
# Start the FinAlly container. Pass --build to force an image rebuild.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=finally
CONTAINER=finally
URL=http://localhost:8000

if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -t "$IMAGE" .
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

ENV_ARGS=()
[[ -f .env ]] && ENV_ARGS=(--env-file .env)

docker run -d --name "$CONTAINER" -p 8000:8000 -v finally-data:/app/db ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} "$IMAGE" >/dev/null

echo "FinAlly is running at $URL"
if command -v open >/dev/null; then open "$URL"; elif command -v xdg-open >/dev/null; then xdg-open "$URL"; fi
