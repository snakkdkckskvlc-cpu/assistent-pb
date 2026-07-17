# ================================================================
# Запускает LanguageTool сервер локально (127.0.0.1:8081) на
# портативном JDK — ничего в системе не трогает. Windows-аналог
# start.sh. Держать запущенным рядом с Ollama, пока работает бэкенд.
#
# Обычно запускается автоматически (свёрнутым) из start.bat, который
# генерирует bootstrap.ps1. Можно запустить и вручную:
#   .\tools\languagetool\start.ps1
# Остановка — закрыть окно или Ctrl+C.
# ================================================================

$ErrorActionPreference = "Stop"

if ($PSScriptRoot) { $root = $PSScriptRoot } else { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

$jdkDir = Get-ChildItem -Path $root -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue | Select-Object -First 1
$ltDir = Join-Path $root "LanguageTool-6.6"

if (-not $jdkDir -or -not (Test-Path $ltDir)) {
    Write-Host "JDK/LanguageTool не найдены — сначала запустите .\tools\languagetool\setup.ps1" -ForegroundColor Red
    exit 1
}

$javaBin = Join-Path $jdkDir.FullName "bin\java.exe"
if (-not (Test-Path $javaBin)) {
    Write-Host "Не найден java.exe внутри $($jdkDir.Name)" -ForegroundColor Red
    exit 1
}

$port = if ($env:LT_PORT) { $env:LT_PORT } else { "8081" }
$classpath = "$ltDir\languagetool-server.jar;$root\dict"

Write-Host "LanguageTool сервер: http://127.0.0.1:$port (словарь: dict\spelling_global.txt)"
& $javaBin -cp $classpath org.languagetool.server.HTTPServer --port $port
