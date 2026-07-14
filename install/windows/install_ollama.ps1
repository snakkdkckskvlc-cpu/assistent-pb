# Установка Ollama и загрузка модели для Fire Safety Assistant.
# Запускать с правами администратора.

$ErrorActionPreference = "Stop"

$model = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "qwen2.5:14b-instruct-q4_K_M" }

Write-Host "=== Fire Safety Assistant: установка Ollama ===" -ForegroundColor Cyan

# 1. Проверить, установлена ли уже Ollama
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    Write-Host "Ollama не найдена. Скачиваем установщик…"
    $installer = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Write-Host "Запускаем установщик (потребуется подтверждение UAC)…"
    Start-Process -FilePath $installer -Wait
    # Обновить PATH в текущей сессии
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
} else {
    Write-Host "Ollama уже установлена: $($ollamaCmd.Source)" -ForegroundColor Green
}

# 2. Запустить службу Ollama (если не запущена)
Write-Host "Проверяем службу Ollama…"
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null
    Write-Host "Ollama уже запущена." -ForegroundColor Green
} catch {
    Write-Host "Запускаем ollama serve в фоне…"
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# 3. Загрузить модель
Write-Host "Скачиваем модель $model (это может занять 10–30 минут)…" -ForegroundColor Cyan
& ollama pull $model
if ($LASTEXITCODE -ne 0) {
    Write-Host "Ошибка при загрузке модели" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Готово ===" -ForegroundColor Green
Write-Host "Модель $model установлена и готова к работе."
Write-Host "Проверить: ollama run $model 'Привет, как дела?'"
