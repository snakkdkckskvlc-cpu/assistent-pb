# ================================================================
# Ассистент ПБ — автоматическая установка на Windows
# ================================================================
# Использование:
#   1. Правая кнопка на этом файле → «Запустить с PowerShell».
#      (Если Windows заблокирует — правой кнопкой на файле,
#      Свойства → внизу «Разблокировать» → OK.)
#   2. Дождаться завершения (~30–40 минут в основном на скачивание модели ~5 ГБ).
#   3. На рабочем столе появится ярлык «Ассистент ПБ» — двойной клик.
#
# Скрипт можно перезапускать — уже установленное пропускается.
# Лог: bootstrap.log рядом со скриптом.
# ================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # ускоряет Invoke-WebRequest в разы

# Определяем корень проекта надёжно (работает и при запуске из .bat)
if ($PSScriptRoot) {
    $root = $PSScriptRoot
} else {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $root

# UTF-8 для корректного вывода кириллицы
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$log = Join-Path $root "bootstrap.log"
try { Start-Transcript -Path $log -Append -ErrorAction SilentlyContinue | Out-Null } catch { }

function Section($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}
function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [X]  $msg" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }

function Test-Command($cmd) {
    $null = Get-Command $cmd -ErrorAction SilentlyContinue
    return $?
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Download-File($url, $out, $desc) {
    Write-Host "  Скачиваю $desc..." -NoNewline
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
        $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host " готово ($sizeMB МБ)" -ForegroundColor Green
    } catch {
        Write-Host " ошибка" -ForegroundColor Red
        throw $_
    }
}

# Оборачиваем ВСЁ в try/catch — если что-то падает, показываем ошибку и НЕ закрываем окно
try {

# ---------------------------------------------------------------
Section "Ассистент ПБ — установка на Windows"
Write-Host "Проект: $root"
Write-Host "Лог:    $log"
Write-Host "Начало: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "PowerShell: $($PSVersionTable.PSVersion) · Windows: $([Environment]::OSVersion.VersionString)"

# ---------------------------------------------------------------
Section "1/7 · Python 3.13"

Refresh-Path
$pythonOk = $false
foreach ($cmd in @("py", "python", "python3")) {
    if (Test-Command $cmd) {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(11|12|13)\.") {
            Ok "Найден: $ver ($cmd)"
            $script:PYTHON = $cmd
            $pythonOk = $true
            break
        }
    }
}

if (-not $pythonOk) {
    Write-Host "  Python 3.11-3.13 не найден. Устанавливаю Python 3.13..."
    if (Test-Command "winget") {
        try {
            winget install --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements | Out-Null
            Refresh-Path
            $script:PYTHON = "py"
            Ok "Python 3.13 установлен через winget"
        } catch {
            Warn "winget не сработал: $($_.Exception.Message)"
        }
    }
    if (-not (Test-Command $script:PYTHON)) {
        $installer = Join-Path $env:TEMP "python-3.13.1-amd64.exe"
        Download-File "https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe" $installer "Python 3.13.1"
        Write-Host "  Запускаю установщик (silent, всё для всех пользователей)..."
        Start-Process -FilePath $installer -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_pip=1" -Wait
        Refresh-Path
        $script:PYTHON = "py"
        if (-not (Test-Command "py")) { Fail "Python не установился. Установите вручную с python.org и запустите скрипт снова." }
        Ok "Python 3.13 установлен"
    }
}

# ---------------------------------------------------------------
Section "2/7 · Ollama + языковая модель"

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $installer = Join-Path $env:TEMP "OllamaSetup.exe"
    Download-File "https://ollama.com/download/OllamaSetup.exe" $installer "Ollama Setup"
    Write-Host "  Запускаю установщик Ollama..."
    Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait
    Refresh-Path
    Start-Sleep -Seconds 3
    if (-not (Test-Command "ollama")) { Fail "Ollama не установилась. Поставьте вручную: https://ollama.com/download/windows" }
    Ok "Ollama установлена"
} else {
    Ok "Ollama уже установлена: $($ollamaCmd.Source)"
}

# Убеждаемся, что служба запущена
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null
    Ok "Служба Ollama работает"
} catch {
    Write-Host "  Запускаю ollama serve..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

$model = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "qwen2.5:7b-instruct" }
$installedModels = (& ollama list) -join "`n"
if ($installedModels -match [regex]::Escape($model)) {
    Ok "Модель $model уже установлена"
} else {
    Write-Host "  Скачиваю модель $model (~4.7 ГБ, может занять 10–30 минут)..."
    & ollama pull $model
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось скачать модель $model" }
    Ok "Модель $model установлена"
}

# ---------------------------------------------------------------
Section "3/7 · Tesseract OCR + русский язык"

if (Test-Command "tesseract") {
    Ok "Tesseract уже установлен: $((Get-Command tesseract).Source)"
} else {
    $installer = Join-Path $env:TEMP "tesseract-setup.exe"
    Download-File "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.3.3.20231005.exe" $installer "Tesseract OCR 5.3.3"
    Write-Host "  Запускаю установщик Tesseract (silent, с русским языком)..."
    Start-Process -FilePath $installer -ArgumentList "/S" -Wait
    Refresh-Path
    if (-not (Test-Command "tesseract")) {
        # Иногда Tesseract не добавляется в PATH автоматически
        $tessDefault = "C:\Program Files\Tesseract-OCR"
        if (Test-Path $tessDefault) {
            [Environment]::SetEnvironmentVariable("Path", "$env:Path;$tessDefault", "Machine")
            $env:Path += ";$tessDefault"
        }
    }
    if (-not (Test-Command "tesseract")) { Fail "Tesseract не установился" }
    Ok "Tesseract установлен"
}

# Проверяем русский языковой пакет; если нет — качаем rus.traineddata из tessdata_best
$langs = (& tesseract --list-langs 2>&1) -join " "
if ($langs -match "\brus\b") {
    Ok "Русский язык: доступен"
} else {
    Warn "Русский языковой пакет не найден — скачиваю..."
    $tessdataDir = "C:\Program Files\Tesseract-OCR\tessdata"
    if (-not (Test-Path $tessdataDir)) { $tessdataDir = Join-Path (Split-Path (Get-Command tesseract).Source) "tessdata" }
    $rusFile = Join-Path $tessdataDir "rus.traineddata"
    Download-File "https://github.com/tesseract-ocr/tessdata_best/raw/main/rus.traineddata" $rusFile "rus.traineddata"
    Ok "Русский языковой пакет установлен"
}

# ---------------------------------------------------------------
Section "4/7 · Poppler (для OCR PDF-сканов)"

$popplerDir = Join-Path $root "poppler"
if (Test-Path (Join-Path $popplerDir "Library\bin\pdftoppm.exe")) {
    Ok "Poppler уже распакован в $popplerDir"
} else {
    $zip = Join-Path $env:TEMP "poppler.zip"
    Download-File "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip" $zip "Poppler 24.08"
    Write-Host "  Распаковываю Poppler..."
    Expand-Archive -Path $zip -DestinationPath $popplerDir -Force
    # Внутри архива есть корневая папка типа "poppler-24.08.0" — переносим содержимое наверх
    $inner = Get-ChildItem $popplerDir -Directory | Select-Object -First 1
    if ($inner -and (Test-Path (Join-Path $inner.FullName "Library\bin"))) {
        Get-ChildItem $inner.FullName | Move-Item -Destination $popplerDir -Force
        Remove-Item $inner.FullName -Recurse -Force
    }
    Ok "Poppler распакован"
}

$popplerBin = Join-Path $popplerDir "Library\bin"
if ($env:Path -notlike "*$popplerBin*") { $env:Path += ";$popplerBin" }

# ---------------------------------------------------------------
Section "5/7 · Python venv + зависимости"

$venv = Join-Path $root "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (Test-Path $venvPython) {
    Ok "venv уже существует: $venv"
} else {
    Write-Host "  Создаю venv..."
    & $script:PYTHON -m venv $venv
    if (-not (Test-Path $venvPython)) { Fail "Не удалось создать venv" }
    Ok "venv создан"
}

Write-Host "  Устанавливаю зависимости из requirements.txt (это займёт 3–10 минут; тянет torch, ~200 МБ)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install --quiet -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Ошибка установки зависимостей — смотрите $log" }
Ok "Python-зависимости установлены"

# ---------------------------------------------------------------
Section "6/7 · Индексация нормативной базы"

$chromaDir = Join-Path $root "data\chroma"
if ((Test-Path $chromaDir) -and (Get-ChildItem $chromaDir -File -Recurse | Measure-Object).Count -gt 0) {
    Ok "База ChromaDB уже проиндексирована"
} else {
    Write-Host "  Индексирую корпус (при первом запуске скачает эмбед-модель ~1.3 ГБ)..."
    Push-Location $root
    try {
        $env:PYTHONPATH = "$root\apps\backend\src;$root\packages\rag\src"
        & $venvPython -m fire_safety_rag.indexer
        if ($LASTEXITCODE -ne 0) { Fail "Ошибка индексации" }
    } finally {
        Pop-Location
    }
    Ok "Корпус проиндексирован"
}

# ---------------------------------------------------------------
Section "7/7 · Ярлык на рабочем столе"

# start.bat — запускает приложение, прописывает PYTHONPATH и PATH
$startBat = Join-Path $root "start.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0poppler\Library\bin;%PATH%"
set "PYTHONPATH=%~dp0apps\backend\src;%~dp0packages\rag\src;%~dp0apps\desktop\src"
if "%LLM_MODEL%"=="" set "LLM_MODEL=$model"
start "" "%~dp0venv\Scripts\pythonw.exe" -m fire_safety_desktop.main
endlocal
"@ | Set-Content -Path $startBat -Encoding ASCII
Ok "start.bat создан"

$iconPath = Join-Path $root "build\icons\AppIcon.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "Ассистент ПБ.lnk"

$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($shortcut)
$lnk.TargetPath = $startBat
$lnk.WorkingDirectory = $root
if (Test-Path $iconPath) { $lnk.IconLocation = $iconPath }
$lnk.Description = "Ассистент по пожарной безопасности"
$lnk.Save()
Ok "Ярлык создан: $shortcut"

# ---------------------------------------------------------------
Section "Готово"
Write-Host ""
Write-Host "Всё установлено." -ForegroundColor Green
Write-Host "Запуск: двойной клик по ярлыку «Ассистент ПБ» на рабочем столе."
Write-Host "Или: $startBat"
Write-Host ""
Write-Host "Тестовые примеры: $root\tests\samples\"
Write-Host "Лог установки:    $log"
Write-Host ""

try { Stop-Transcript | Out-Null } catch { }
Write-Host "Нажмите Enter, чтобы закрыть..."
try { [Console]::ReadLine() | Out-Null } catch { Read-Host }

} catch {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host "  ОШИБКА УСТАНОВКИ" -ForegroundColor Red
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Полный текст ошибки:" -ForegroundColor Yellow
    Write-Host $_.ScriptStackTrace
    Write-Host ""
    Write-Host "Лог установки: $log" -ForegroundColor Cyan
    Write-Host "Пришлите этот лог + скриншот этого окна разработчику." -ForegroundColor Cyan
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host "Нажмите Enter, чтобы закрыть..."
    try { [Console]::ReadLine() | Out-Null } catch { Read-Host }
    exit 1
}
