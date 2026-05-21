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

echo "Esperando a Langfuse v3 (web)..."
until curl -fsS http://localhost:3000/api/public/health > /dev/null 2>&1; do
  sleep 2
done

echo ""
echo "✓ Postgres   → localhost:5432 (user=saas pass=saas db=saas)"
echo "✓ Redis      → localhost:6379 (cola ARQ)"
echo "✓ Langfuse   → http://localhost:3000 (dev@local.dev / changeme123)"
echo "  Stack v3: langfuse-web, langfuse-worker, clickhouse, minio (9090), langfuse-redis (interno)"
echo ""
echo "Tras migrar a v3: usa las API keys del headless init (pk-lf-mi-saas-dev-local) en Infisical"
echo "y reinicia el worker ARQ. Si la BD venía de v2, borra docker/data/langfuse-db/ antes del up."
