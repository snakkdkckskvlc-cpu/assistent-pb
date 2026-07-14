# Собирает Windows .exe вокруг fire_safety_desktop.main через PyInstaller.
# Запускать НА WINDOWS-МАШИНЕ (PyInstaller не кросс-компилятор).
#
# Требования:
#   1. Уже установлены Python 3.11+ и venv (см. docs/07-ops/install-windows.md)
#   2. Уже установлена Ollama и модель qwen2.5:14b-instruct-q4_K_M
#   3. Уже установлен Tesseract с русским
#   4. Уже проиндексирован корпус (python -m fire_safety_rag.indexer)
#
# Итог: dist\АссистентПБ\АссистентПБ.exe

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root "venv\Scripts\python.exe"
$icon = Join-Path $root "build\icons\AppIcon.ico"

if (-not (Test-Path $venvPython)) {
    Write-Error "venv не найден: $venvPython. Сначала: python -m venv venv; .\venv\Scripts\pip install -e apps\backend -e apps\desktop -e packages\rag"
    exit 1
}

if (-not (Test-Path $icon)) {
    Write-Host "Иконка не найдена — генерирую…" -ForegroundColor Yellow
    & $venvPython "$root\scripts\make_icons.py"
}

Write-Host "== Проверяем PyInstaller =="
& $venvPython -m pip install --quiet pyinstaller

# Точка входа для PyInstaller — тонкий wrapper, вызывающий fire_safety_desktop.main:main
$entry = Join-Path $env:TEMP "fire_safety_entry.py"
@"
import sys
from pathlib import Path

# В bundle пакеты уже в sys.path — этот блок нужен только при сборке
ROOT = Path(__file__).resolve().parent
for p in [ROOT / 'apps' / 'backend' / 'src', ROOT / 'packages' / 'rag' / 'src', ROOT / 'apps' / 'desktop' / 'src']:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fire_safety_desktop.main import main
main()
"@ | Set-Content -Path $entry -Encoding UTF8

Write-Host "== Собираем .exe (это займёт 2-5 минут) =="
Push-Location $root
try {
    & $venvPython -m PyInstaller `
        --name "АссистентПБ" `
        --windowed `
        --icon $icon `
        --paths "apps\backend\src" `
        --paths "packages\rag\src" `
        --paths "apps\desktop\src" `
        --add-data "apps\desktop\frontend;apps\desktop\frontend" `
        --add-data "apps\backend\src\fire_safety_backend\resources;fire_safety_backend\resources" `
        --add-data "packages\rag\corpus;packages\rag\corpus" `
        --hidden-import uvicorn.logging `
        --hidden-import uvicorn.loops `
        --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols `
        --hidden-import uvicorn.protocols.http `
        --hidden-import uvicorn.protocols.http.auto `
        --hidden-import uvicorn.protocols.websockets `
        --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan `
        --hidden-import uvicorn.lifespan.on `
        --collect-all fire_safety_backend `
        --collect-all fire_safety_desktop `
        --collect-all fire_safety_rag `
        --collect-all chromadb `
        --collect-all sentence_transformers `
        --noconfirm `
        $entry
}
finally {
    Pop-Location
}

$exe = Join-Path $root "dist\АссистентПБ\АссистентПБ.exe"
if (Test-Path $exe) {
    Write-Host ""
    Write-Host "=== ГОТОВО ===" -ForegroundColor Green
    Write-Host "EXE: $exe"
    Write-Host ""
    Write-Host "Что дальше:"
    Write-Host "  1. Скопируйте всю папку dist\АссистентПБ\ куда угодно (например, C:\Programs\)."
    Write-Host "  2. Создайте ярлык на АссистентПБ.exe и положите на рабочий стол."
    Write-Host "  3. Иконка и название появятся в Пуске автоматически."
    Write-Host ""
    Write-Host "ВАЖНО: data\ (для chroma, uploads, outputs) должен быть рядом с .exe."
} else {
    Write-Error "Сборка не удалась — .exe не появился"
    exit 1
}
