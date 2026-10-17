# Stop and remove the FinAlly container. The finally-data volume is kept.
docker rm -f finally *> $null
Write-Host "FinAlly stopped"
