docker ps -a --format "{{.Names}}" | Where-Object { $_ -like "inframind*" } | ForEach-Object { docker rm -f $_ | Out-Null }

astro dev start --no-browser --settings-file /dev/null

$scheduler = $null
for ($i = 0; $i -lt 24 -and -not $scheduler; $i++) {
    Start-Sleep -Seconds 5
    $scheduler = docker ps --format "{{.Names}}" | Where-Object { $_ -match "scheduler" } | Select-Object -First 1
}
if (-not $scheduler) { Write-Host "ERROR: Scheduler not found. Check 'astro dev logs'." -ForegroundColor Red; exit 1 }

for ($i = 0; $i -lt 12; $i++) {
    docker exec $scheduler airflow version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
}

docker exec $scheduler airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec $scheduler airflow variables set INFRAMIND_S3_BUCKET inframind-data-hub
docker exec $scheduler airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $scheduler airflow variables set INFRAMIND_MAX_LOGS 1
docker exec $scheduler airflow variables set INFRAMIND_FORCE_REBUILD false

Write-Host "Airflow UI:  http://localhost:8080  (admin/admin)" -ForegroundColor Cyan
Write-Host "Grafana:     http://localhost:3000  (admin/admin)" -ForegroundColor Cyan
Write-Host "Prometheus:  http://localhost:9090" -ForegroundColor Cyan
