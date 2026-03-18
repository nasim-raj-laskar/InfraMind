# restart.ps1 - clean restart for InfraMind Astro project
# Usage: .\restart.ps1

Write-Host "Stopping all InfraMind containers..." -ForegroundColor Yellow
docker ps -a --format "{{.Names}}" | Where-Object { $_ -like "infra-mind_*" } | ForEach-Object {
    docker rm -f $_ | Out-Null
}

# Detect network name from any existing infra-mind network (name is stable per project folder)
$network = docker network ls --format "{{.Name}}" | Where-Object { $_ -like "infra-mind_*_airflow" } | Select-Object -First 1

if ($network) {
    Write-Host "Patching network name: $network" -ForegroundColor Cyan
    (Get-Content docker-compose.override.yml) `
        -replace "name: infra-mind_.*_airflow", "name: $network" |
        Set-Content docker-compose.override.yml
} else {
    Write-Host "No existing network found - will detect after first start" -ForegroundColor Yellow
}

Write-Host "Starting Astro..." -ForegroundColor Yellow
astro dev start --no-browser --settings-file /dev/null

# If network wasn't found before (first ever run), detect and patch now for next restart
if (-not $network) {
    $network = docker network ls --format "{{.Name}}" | Where-Object { $_ -like "infra-mind_*_airflow" } | Select-Object -First 1
    Write-Host "First run - saving network name: $network" -ForegroundColor Cyan
    (Get-Content docker-compose.override.yml) `
        -replace "name: infra-mind_.*_airflow", "name: $network" |
        Set-Content docker-compose.override.yml
    Write-Host "Run .\restart.ps1 again to apply monitoring on the correct network." -ForegroundColor Yellow
}

Write-Host "Waiting for scheduler to be ready..." -ForegroundColor Yellow
$scheduler = $null
$attempts  = 0
while (-not $scheduler -and $attempts -lt 24) {
    Start-Sleep -Seconds 5
    $attempts++
    $scheduler = docker ps --format "{{.Names}}" | Where-Object { $_ -match "scheduler" } | Select-Object -First 1
}

if (-not $scheduler) {
    Write-Host "ERROR: Scheduler container not found after 2 minutes. Check 'astro dev logs'." -ForegroundColor Red
    exit 1
}
Write-Host "Scheduler: $scheduler" -ForegroundColor Cyan

# Wait for Airflow DB migrations to finish before applying config
Write-Host "Waiting for Airflow to finish initialising..." -ForegroundColor Yellow
$ready = $false
$attempts = 0
while (-not $ready -and $attempts -lt 12) {
    Start-Sleep -Seconds 5
    $attempts++
    $out = docker exec $scheduler airflow version 2>&1
    if ($LASTEXITCODE -eq 0) { $ready = $true }
}

if (-not $ready) {
    Write-Host "WARNING: Airflow not ready - pool/variables may fail. Retry manually." -ForegroundColor Yellow
}

Write-Host "Applying pool and variables..." -ForegroundColor Yellow
docker exec $scheduler airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec $scheduler airflow variables set INFRAMIND_S3_BUCKET inframind-data-hub
docker exec $scheduler airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $scheduler airflow variables set INFRAMIND_MAX_LOGS 1
docker exec $scheduler airflow variables set INFRAMIND_FORCE_REBUILD false

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Write-Host "  Airflow UI:  http://localhost:8080  (admin/admin)" -ForegroundColor Cyan
Write-Host "  Grafana:     http://localhost:3000  (admin/admin)" -ForegroundColor Cyan
Write-Host "  Prometheus:  http://localhost:9090" -ForegroundColor Cyan
