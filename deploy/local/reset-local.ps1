[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$envFile = Join-Path $PSScriptRoot ".env.local"
$exampleEnv = Join-Path $PSScriptRoot ".env.local.example"
$selectedEnv = if (Test-Path $envFile) { $envFile } else { $exampleEnv }

if (-not $Force) {
    $answer = Read-Host "Удалить локальные PostgreSQL, Redis, MinIO и ClamAV volumes? [yes/NO]"
    if ($answer -ne "yes") {
        Write-Host "Сброс отменён."
        exit 0
    }
}

docker compose `
    --env-file $selectedEnv `
    -f (Join-Path $root "compose.local.yml") `
    down `
    --volumes `
    --remove-orphans

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось сбросить локальный стек."
}

Write-Host "Локальные контейнеры и данные удалены. Файл .env.local сохранён."
