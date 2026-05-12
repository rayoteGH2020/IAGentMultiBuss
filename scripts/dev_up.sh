#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Levantando servicios..."
docker compose -f docker/docker-compose.yml up -d

echo "Esperando a Postgres..."
until docker compose -f docker/docker-compose.yml exec -T postgres pg_isready -U saas > /dev/null 2>&1; do
  sleep 1
done

echo "Esperando a Redis..."
until docker compose -f docker/docker-compose.yml exec -T redis redis-cli ping > /dev/null 2>&1; do
  sleep 1
done

echo "Esperando a Langfuse..."
until curl -fsS http://localhost:3000/api/public/health > /dev/null 2>&1; do
  sleep 1
done

echo ""
echo "✓ Postgres   → localhost:5432 (user=saas pass=saas db=saas)"
echo "✓ Redis      → localhost:6379"
echo "✓ Langfuse   → http://localhost:3000 (dev@local.dev / changeme123)"
echo ""
echo "Recuerda registrar las API keys de Langfuse en Infisical (LANGFUSE_*); ver Agents.md §2."
