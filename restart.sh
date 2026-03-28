#!/usr/bin/env bash
set -euo pipefail

docker ps -a --format "{{.Names}}" | grep "^inframind" | xargs -r docker rm -f

astro dev start --no-browser --settings-file /dev/null

scheduler=""
for i in $(seq 1 24); do
    sleep 5
    scheduler=$(docker ps --format "{{.Names}}" | grep "scheduler" | head -1 || true)
    [ -n "$scheduler" ] && break
done
[ -z "$scheduler" ] && echo "ERROR: Scheduler not found. Check 'astro dev logs'." && exit 1

for i in $(seq 1 12); do
    docker exec "$scheduler" airflow version &>/dev/null && break
    sleep 5
done

docker exec "$scheduler" airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec "$scheduler" airflow variables set INFRAMIND_S3_BUCKET inframind-data-hub
docker exec "$scheduler" airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec "$scheduler" airflow variables set INFRAMIND_MAX_LOGS 1
docker exec "$scheduler" airflow variables set INFRAMIND_FORCE_REBUILD false

echo "Airflow UI:  http://localhost:8080  (admin/admin)"
echo "Grafana:     http://localhost:3000  (admin/admin)"
echo "Prometheus:  http://localhost:9090"
