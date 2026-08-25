#!/usr/bin/env bash
# One-command bootstrap: from a fresh clone to a running system.
#
#   ./scripts/bootstrap.sh
#
# Brings up Postgres+pgvector, runs migrations, and starts the API and
# worker. LLM_PROVIDER defaults to "fake" (see .env.example) so this works
# with no API key - swap to LLM_PROVIDER=openai + LLM_API_KEY in .env for
# real model calls.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (LLM_PROVIDER=fake by default)."
fi

docker compose up --build -d db
echo "Waiting for Postgres to be healthy..."
until docker compose exec -T db pg_isready -U postgres > /dev/null 2>&1; do
  sleep 1
done

docker compose up --build migrate
docker compose up --build -d api worker watcher frontend

echo ""
echo "System is up."
echo "  API:      http://localhost:8000/health"
echo "  API docs: http://localhost:8000/docs"
echo "  Review UI: http://localhost:5173"
echo "  Watched folder: drop a .pdf/.docx/.txt/.md into the doctask_watched"
echo "    volume and it's ingested and run automatically within ~2s -"
echo "    docker compose exec watcher ls /app/data/watched"
echo ""
echo "Try it:"
echo "  curl -H 'x-api-key: dev-local-key' http://localhost:8000/health"
echo ""
echo "Logs:   docker compose logs -f api worker watcher frontend"
echo "Stop:   docker compose down"
