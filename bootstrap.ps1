# -*- coding: utf-8 -*-

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


$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host "  DLYA USTANOVKI TREBUYUTSYA PRAVA ADMINISTRATORA!" -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host ""
    $scriptPath = $MyInvocation.MyCommand.Path
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    Start-Process powershell -Verb RunAs -ArgumentList $arguments
    exit
}

Write-Host "  [OK] Running with administrator privileges" -ForegroundColor Green
Write-Host ""

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

if ($PSScriptRoot) { $root = $PSScriptRoot } else { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$log = Join-Path $root "bootstrap.log"
try { Start-Transcript -Path $log -Append -ErrorAction SilentlyContinue | Out-Null } catch { }

function Section($msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [X] $msg" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }
function Test-Command($cmd) { $null = Get-Command $cmd -ErrorAction SilentlyContinue; return $? }
function Refresh-Path { $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User") }

function Download-File($url, $out, $desc) {
    Write-Host "  Downloading $desc..." -NoNewline
    $maxRetries = 5
    $retryDelay = 10
    for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
        try {
            if ($attempt -gt 1) { Write-Host "`n  Attempt $attempt of $maxRetries..." -ForegroundColor Yellow }
            if ($attempt -le 3) {
                Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 120
            } else {
                $webClient = New-Object System.Net.WebClient
                $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
                $webClient.DownloadFile($url, $out)
                $webClient.Dispose()
            }
            $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
            Write-Host " done ($sizeMB MB)" -ForegroundColor Green
            return
        } catch {
            if ($attempt -lt $maxRetries) {
                Write-Host " error, retry in $retryDelay sec..." -ForegroundColor Yellow
                Start-Sleep -Seconds $retryDelay
                $retryDelay += 5
            } else {
                Write-Host " error" -ForegroundColor Red
                throw $_
            }
        }
    }
}

try {

Section "Assistant PB - Installation on Windows"
Write-Host "Project: $root"
Write-Host "Log: $log"
Write-Host "Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ================================================================
# STEP 1: PYTHON 3.12
# ================================================================

Section "1/8 Python 3.12"
Refresh-Path
$pythonOk = $false
foreach ($cmd in @("py", "python", "python3")) {
    if (Test-Command $cmd) {
        $ver = & $cmd --version 2>&1
        if ($ver -match 'Python 3\.(11|12)\.') {
            Ok "Found: $ver ($cmd)"
            $script:PYTHON = $cmd
            $pythonOk = $true
            break
        }
    }
}

if (-not $pythonOk) {
    Write-Host "  Python 3.11-3.12 not found. Installing Python 3.12..."
    if (Test-Command "winget") {
        try {
            winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements | Out-Null
            Refresh-Path
            $script:PYTHON = "py"
            Ok "Python 3.12 installed via winget"
        } catch {
            Warn "winget failed"
        }
    }
    if (-not (Test-Command $script:PYTHON)) {
        $installer = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
        Download-File "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" $installer "Python 3.12.8"
        Write-Host "  Running installer (silent, all users)..."
        Start-Process -FilePath $installer -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_pip=1" -Wait
        Refresh-Path
        $script:PYTHON = "py"
        if (-not (Test-Command "py")) { Fail "Python did not install. Please install manually." }
        Ok "Python 3.12 installed"
    }
}

# ================================================================
# STEP 2: OLLAMA + MODEL
# ================================================================

Section "2/8 Ollama + language model"

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $installer = Join-Path $env:TEMP "OllamaSetup.exe"
    if (Test-Path $installer) { Remove-Item $installer -Force -ErrorAction SilentlyContinue }
    
    Write-Host "  Downloading Ollama..."
    try {
        Download-File "https://ollama.com/download/OllamaSetup.exe" $installer "Ollama Setup"
    } catch {
        Download-File "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe" $installer "Ollama Setup (GitHub)"
    }
    
    Write-Host "  Running Ollama installer..."
    Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait
    Refresh-Path
    Start-Sleep -Seconds 3
    
    if (-not (Test-Command "ollama")) { Fail "Ollama did not install. Install manually." }
    Ok "Ollama installed"
} else {
    Ok "Ollama already installed"
}

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null
    Ok "Ollama service is running"
} catch {
    Write-Host "  Starting ollama serve..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

$model = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "qwen2.5:7b-instruct" }
$installedModels = (& ollama list) -join "`n"
if ($installedModels -match [regex]::Escape($model)) {
    Ok "Model $model already installed"
} else {
    Write-Host "  Downloading model $model (~4.7 GB, may take 10-30 minutes)..."
    & ollama pull $model
    if ($LASTEXITCODE -ne 0) { Fail "Failed to download model $model" }
    Ok "Model $model installed"
}

# ================================================================
# STEP 3: TESSERACT
# ================================================================

Section "3/8 Tesseract OCR"
if (Test-Command "tesseract") {
    Ok "Tesseract already installed"
} else {
    $localInstaller = Join-Path $root "installers\tesseract-ocr-w64-setup-5.3.3.20231005.exe"
    $tempInstaller = Join-Path $env:TEMP "tesseract-setup.exe"
    
    if (Test-Path $localInstaller) {
        Write-Host "  Found local installer" -ForegroundColor Green
        Copy-Item $localInstaller $tempInstaller -Force
    } else {
        Write-Host "  Downloading Tesseract..." -ForegroundColor Yellow
        try {
            Download-File "https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe" $tempInstaller "Tesseract OCR"
        } catch {
            Warn "Could not download Tesseract. Skipping..."
        }
    }
    
    if (Test-Path $tempInstaller) {
        Write-Host "  Installing Tesseract..."
        Start-Process -FilePath $tempInstaller -ArgumentList "/S" -Wait
        Refresh-Path
        Start-Sleep -Seconds 3
        if (Test-Command "tesseract") { Ok "Tesseract installed!" }
    }
}

# ================================================================
# STEP 4: POPPLER
# ================================================================

Section "4/8 Poppler"
$popplerDir = Join-Path $root "poppler"
$localZip = Join-Path $root "installers\poppler.zip"
$tempZip = Join-Path $env:TEMP "poppler.zip"

if (Test-Path (Join-Path $popplerDir "Library\bin\pdftoppm.exe")) {
    Ok "Poppler already extracted"
} else {
    if (Test-Path $localZip) {
        Write-Host "  Found local poppler.zip" -ForegroundColor Green
        Copy-Item $localZip $tempZip -Force
    } else {
        Write-Host "  Downloading Poppler..." -ForegroundColor Yellow
        try {
            Download-File "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip" $tempZip "Poppler"
        } catch {
            Warn "Could not download Poppler. Skipping..."
        }
    }
    
    if (Test-Path $tempZip) {
        Write-Host "  Extracting Poppler..."
        Expand-Archive -Path $tempZip -DestinationPath $popplerDir -Force
        $inner = Get-ChildItem $popplerDir -Directory | Where-Object { $_.Name -like "poppler-*" } | Select-Object -First 1
        if ($inner -and (Test-Path (Join-Path $inner.FullName "Library\bin"))) {
            Get-ChildItem $inner.FullName | Move-Item -Destination $popplerDir -Force
            Remove-Item $inner.FullName -Recurse -Force
        }
        Ok "Poppler extracted"
    }
}

# ================================================================
# STEP 5: VENV + DEPENDENCIES
# ================================================================

Section "5/8 Python venv + dependencies"

$venv = Join-Path $root "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$venvPip = Join-Path $venv "Scripts\pip.exe"

if (Test-Path $venv) {
    Write-Host "  Removing old venv..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Write-Host "  Creating venv..." -ForegroundColor Yellow
& $script:PYTHON -m venv $venv
if (-not (Test-Path $venvPython)) { Fail "Failed to create venv" }
Ok "venv created"

# ================================================================
# УСТАНОВКА ВСЕХ ПАКЕТОВ (ВКЛЮЧАЯ ДЛЯ DESKTOP)
# ================================================================

Write-Host "  Installing all required packages..." -ForegroundColor Yellow

# Полный список пакетов
$packages = @(
    "numpy",
    "nltk",
    "chromadb",
    "langchain",
    "torch",
    "transformers",
    "sentence-transformers",
    "pywebview",
    "fastapi",
    "uvicorn",
    "pydantic",
    "requests",
    "pillow"
)

$failed = @()

foreach ($pkg in $packages) {
    Write-Host "  Installing $pkg..." -NoNewline
    & $venvPip install $pkg --quiet --no-cache-dir --prefer-binary --timeout 300 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
    } else {
        Write-Host " FAILED" -ForegroundColor Red
        $failed += $pkg
    }
}

if ($failed.Count -gt 0) {
    Warn "Failed packages: $($failed -join ', ')"
    Write-Host "  Retrying failed packages..." -ForegroundColor Yellow
    foreach ($pkg in $failed) {
        Write-Host "  Retrying $pkg..." -NoNewline
        & $venvPip install $pkg --quiet --no-cache-dir 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host " OK" -ForegroundColor Green
        } else {
            Write-Host " FAILED" -ForegroundColor Red
        }
    }
}

# ================================================================
# ПРОВЕРКА УСТАНОВКИ webview
# ================================================================

Write-Host "`n  Verifying webview installation..." -ForegroundColor Yellow
$webviewCheck = & $venvPython -c "import webview; print('OK')" 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok "webview is installed!"
} else {
    Warn "webview not found. Force installing..."
    & $venvPip install pywebview --force-reinstall --quiet --no-cache-dir
    $webviewCheck = & $venvPython -c "import webview; print('OK')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Ok "webview installed successfully!"
    } else {
        Warn "webview installation failed. Please install manually: pip install pywebview"
    }
}

# ================================================================
# УСТАНОВКА ПРОЕКТНЫХ ПАКЕТОВ
# ================================================================

Write-Host "`n  Installing project packages..." -ForegroundColor Yellow

$projectPaths = @(
    @{Name="apps\backend"; Path=Join-Path $root "apps\backend"},
    @{Name="apps\desktop"; Path=Join-Path $root "apps\desktop"},
    @{Name="packages\rag"; Path=Join-Path $root "packages\rag"}
)

foreach ($proj in $projectPaths) {
    $projPath = $proj.Path
    $projName = $proj.Name
    
    Write-Host "  Installing $projName..." -NoNewline
    
    $srcPath = Join-Path $projPath "src"
    if (Test-Path $srcPath) {
        $pthFile = Join-Path $venv "Lib\site-packages\_$($projName.Replace('\','_')).pth"
        $srcPath | Set-Content -Path $pthFile -Encoding UTF8
        Write-Host " OK (path added: $srcPath)" -ForegroundColor Green
    } else {
        Write-Host " SKIPPED (src not found)" -ForegroundColor Yellow
    }
}

# ================================================================
# ФИНАЛЬНАЯ ПРОВЕРКА
# ================================================================

Write-Host "`n  Final verification..." -ForegroundColor Yellow

$checkPackages = @("numpy", "nltk", "chromadb", "langchain", "torch", "transformers", "pywebview")
$allOk = $true

foreach ($pkg in $checkPackages) {
    $check = & $venvPython -c "import $pkg; print('OK')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    $pkg: OK" -ForegroundColor Green
    } else {
        Write-Host "    $pkg: MISSING" -ForegroundColor Red
        $allOk = $false
    }
}

if ($allOk) {
    Ok "All packages installed successfully!"
} else {
    Warn "Some packages are missing. Please check manually."
}

Ok "Python dependencies installation completed"

# ================================================================
# STEP 6: INDEXING
# ================================================================

Section "6/8 Indexing regulatory database"
$docsDir = Join-Path $root "data\documents"
if (-not (Test-Path $docsDir)) {
    New-Item -ItemType Directory -Path $docsDir -Force | Out-Null
}
$docCount = (Get-ChildItem $docsDir -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "  Documents found: $docCount" -ForegroundColor Gray

if ($docCount -gt 0) {
    Write-Host "  Indexing corpus (first run will download embedding model ~1.3 GB)..." -ForegroundColor Yellow
    Push-Location $root
    try {
        $env:PYTHONPATH = "$root\apps\backend\src;$root\packages\rag\src"
        & $venvPython -m fire_safety_rag.indexer
        if ($LASTEXITCODE -eq 0) { Ok "Corpus indexed" }
    } catch {
        Warn "Indexing error: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
} else {
    Warn "No documents to index. Skipping."
}

# ================================================================
# STEP 7: DESKTOP SHORTCUT
# ================================================================

Section "7/8 Desktop shortcut"

$startBat = Join-Path $root "start.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0poppler\Library\bin;%PATH%"
set "PYTHONPATH=%~dp0apps\backend\src;%~dp0packages\rag\src;%~dp0apps\desktop\src;%~dp0apps\desktop"
if "%LLM_MODEL%"=="" set "LLM_MODEL=$model"
start "" "%~dp0venv\Scripts\pythonw.exe" -m fire_safety_desktop.main
endlocal
"@ | Set-Content -Path $startBat -Encoding ASCII
Ok "start.bat created"

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "Assistant PB.lnk"

try {
    $wsh = New-Object -ComObject WScript.Shell
    $lnk = $wsh.CreateShortcut($shortcut)
    $lnk.TargetPath = $startBat
    $lnk.WorkingDirectory = $root
    $lnk.Description = "Fire Safety Assistant"
    $lnk.Save()
    Ok "Shortcut created: $shortcut"
} catch {
    Warn "Could not create shortcut"
}

# ================================================================
# DONE
# ================================================================

Section "Done"
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  INSTALLATION COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Launch: double-click 'Assistant PB' shortcut on desktop" -ForegroundColor Cyan
Write-Host "  Or: $startBat" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Log: $log" -ForegroundColor Gray
Write-Host ""

try { Stop-Transcript | Out-Null } catch { }
Write-Host "Press Enter to close..."
Read-Host

} catch {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host "  INSTALLATION ERROR" -ForegroundColor Red
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Full error details:" -ForegroundColor Yellow
    Write-Host $_.ScriptStackTrace
    Write-Host ""
    Write-Host "Log: $log" -ForegroundColor Cyan
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host "Press Enter to close..."
    Read-Host
    exit 1
}
