[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$envFile = Join-Path $PSScriptRoot ".env.local"
$exampleEnv = Join-Path $PSScriptRoot ".env.local.example"
$selectedEnv = if (Test-Path $envFile) { $envFile } else { $exampleEnv }

docker compose `
    --env-file $selectedEnv `
    -f (Join-Path $root "compose.local.yml") `
    down `
    --remove-orphans

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось остановить локальный стек."
}
