#!/usr/bin/env bash
# Levanta todo el sistema con una sola instrucción: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || cp .env.example .env
API_PORT="$(grep -E '^API_PORT=' .env | cut -d= -f2)"
API_PORT="${API_PORT:-8000}"
docker compose up -d --build

echo "Esperando a que la API esté lista (la primera vez descarga el modelo de embeddings)..."
until curl -sf "http://localhost:${API_PORT}/health" > /dev/null; do sleep 3; done

echo
echo "API:            http://localhost:${API_PORT}/docs"
echo "Phoenix:        http://localhost:6006"
echo "Prueba de carga: python scripts/load_test.py --url http://localhost:${API_PORT}"
