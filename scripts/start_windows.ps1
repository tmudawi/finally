# Start the FinAlly container. Pass -Build to force an image rebuild.
param([switch]$Build)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Image = "finally"
$Container = "finally"
$Url = "http://localhost:8000"

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

docker rm -f $Container *> $null

$EnvArgs = @()
if (Test-Path .env) { $EnvArgs = @("--env-file", ".env") }

docker run -d --name $Container -p 8000:8000 -v finally-data:/app/db @EnvArgs $Image | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "FinAlly is running at $Url"
Start-Process $Url
