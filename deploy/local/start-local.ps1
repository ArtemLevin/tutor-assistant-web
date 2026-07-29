[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $root "compose.local.yml"
$envFile = Join-Path $PSScriptRoot ".env.local"
$exampleEnv = Join-Path $PSScriptRoot ".env.local.example"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI не найден. Установите и запустите Docker Desktop."
}

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Команда 'docker compose' недоступна."
}

if (-not (Test-Path $envFile)) {
    Copy-Item $exampleEnv $envFile
    Write-Host "Создан $envFile с локальными demo-настройками."
}

$buildContexts = @(
    (Join-Path $root "../tutorboard"),
    (Join-Path $root "../geometryos")
)
foreach ($context in $buildContexts) {
    if (-not (Test-Path $context)) {
        throw "Не найден соседний репозиторий: $context"
    }
}

$compose = @("compose", "--env-file", $envFile, "-f", $composeFile)
& docker @compose config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Конфигурация compose.local.yml не прошла проверку."
}

$up = $compose + @("up", "--detach", "--remove-orphans")
if (-not $SkipBuild) {
    $up += "--build"
}
& docker @up
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось запустить локальный стек."
}

if (-not $SkipSmoke) {
    & docker @compose --profile smoke run --rm smoke
    if ($LASTEXITCODE -ne 0) {
        & docker @compose logs --tail 120 gateway web tutorboard geometryos
        throw "Локальный smoke-тест завершился ошибкой."
    }
}

$values = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        $values[$matches[1].Trim()] = $matches[2].Trim()
    }
}
$port = if ($values["LOCAL_PORT"]) { $values["LOCAL_PORT"] } else { "8080" }
$email = if ($values["BOOTSTRAP_ADMIN_EMAIL"]) {
    $values["BOOTSTRAP_ADMIN_EMAIL"]
} else {
    "admin@localhost"
}
$password = if ($values["BOOTSTRAP_ADMIN_PASSWORD"]) {
    $values["BOOTSTRAP_ADMIN_PASSWORD"]
} else {
    "local-demo-password"
}

Write-Host ""
Write-Host "Единое приложение запущено: http://localhost:$port/"
Write-Host "TutorBoard: http://localhost:$port/board/"
Write-Host "Логин: $email"
Write-Host "Пароль: $password"
