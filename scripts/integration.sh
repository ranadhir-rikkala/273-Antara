#!/usr/bin/env sh
cd "$(dirname "$0")/.."
network=antara-integration
database=antara-integration-db
cleanup() {
  docker rm -f "$database" >/dev/null 2>&1
  docker network rm "$network" >/dev/null 2>&1
}
trap cleanup EXIT
cleanup
docker network create "$network" >/dev/null
docker run -d --name "$database" --network "$network" \
  -e POSTGRES_USER=antara -e POSTGRES_PASSWORD=antara -e POSTGRES_DB=antara \
  -v "$PWD/deploy/sql:/docker-entrypoint-initdb.d:ro" pgvector/pgvector:pg16 >/dev/null
until docker exec "$database" psql -U antara -d antara -tAc "SELECT 1 FROM answer_cache LIMIT 1" >/dev/null 2>&1; do
  sleep 1
done
docker run --rm --network "$network" -v "$PWD":/src -w /src \
  -e DATABASE_URL="postgresql://antara:antara@$database:5432/antara" python:3.12-slim sh -c \
  "pip install -q -e '.[dev]' >/dev/null 2>&1 && pytest -q -m integration -p no:cacheprovider"
status=$?
rm -rf antara.egg-info
find . -name __pycache__ -prune -exec rm -rf {} +
exit $status
