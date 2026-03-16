# restart.ps1 — clean restart for InfraMind Astro project
# Usage: .\restart.ps1

Write-Host "Stopping all InfraMind containers..." -ForegroundColor Yellow
docker ps -a --format "{{.Names}}" | Where-Object { $_ -like "infra-mind_*" } | ForEach-Object {
    docker rm -f $_ | Out-Null
}

Write-Host "Starting Astro..." -ForegroundColor Yellow
astro dev start --no-browser --settings-file /dev/null

Write-Host "Waiting for scheduler to be ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 15

$scheduler = docker ps --format "{{.Names}}" | Where-Object { $_ -like "infra-mind_*scheduler*" }
Write-Host "Scheduler: $scheduler" -ForegroundColor Cyan

Write-Host "Applying pool and variables..." -ForegroundColor Yellow
docker exec $scheduler airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec $scheduler airflow variables set INFRAMIND_S3_BUCKET inframind-data-hub
docker exec $scheduler airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $scheduler airflow variables set INFRAMIND_MAX_LOGS 3
docker exec $scheduler airflow variables set INFRAMIND_FORCE_REBUILD false

Write-Host "Done! Airflow UI: http://localhost:8080" -ForegroundColor Green
