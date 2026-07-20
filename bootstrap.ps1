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

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ================================================================
# АВТОМАТИЧЕСКИЙ ЗАПУСК ОТ ИМЕНИ АДМИНИСТРАТОРА
# ================================================================

# Проверяем, есть ли права администратора
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host "  This script requires administrator privileges!" -ForegroundColor Yellow
    Write-Host "  Restarting with administrator rights..." -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host ""
    
    # Сохраняем текущую директорию
    $scriptPath = $MyInvocation.MyCommand.Path
    
    # Создаём новый процесс с правами администратора
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    Start-Process powershell -Verb RunAs -ArgumentList $arguments
    
    # Закрываем текущий скрипт
    exit
}

Write-Host "  [OK] Running with administrator privileges" -ForegroundColor Green

# Определяем корневую папку проекта
if ($PSScriptRoot) {
    $root = $PSScriptRoot
} else {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $root

# Настройка кодировки UTF-8
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

# Настройки для обхода проблем с сетью
try {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls11 -bor [System.Net.SecurityProtocolType]::Tls
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    [System.Net.ServicePointManager]::DefaultConnectionLimit = 100
    [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
} catch { }

# Путь к лог-файлу
$log = Join-Path $root "bootstrap.log"
try { Start-Transcript -Path $log -Append -ErrorAction SilentlyContinue | Out-Null } catch { }

# ================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ================================================================

function Section($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [X] $msg" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }

function Test-Command($cmd) {
    $null = Get-Command $cmd -ErrorAction SilentlyContinue
    return $?
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ================================================================
# ФУНКЦИЯ СКАЧИВАНИЯ С ПОВТОРАМИ
# ================================================================

function Download-File($url, $out, $desc) {
    Write-Host "  Downloading $desc..." -NoNewline
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls11 -bor [System.Net.SecurityProtocolType]::Tls
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    } catch { }
    
    $maxRetries = 5
    $retryDelay = 10
    for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
        try {
            if ($attempt -gt 1) {
                Write-Host "`n  Attempt $attempt of $maxRetries..." -ForegroundColor Yellow
            }
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
                if (Test-Command "curl") {
                    Write-Host "  Trying via curl..." -ForegroundColor Yellow
                    try {
                        & curl -L -o $out $url --connect-timeout 120 --ssl-no-revoke --insecure
                        if ($LASTEXITCODE -eq 0) {
                            $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
                            Write-Host " done via curl ($sizeMB MB)" -ForegroundColor Green
                            return
                        }
                    } catch { }
                }
                throw $_
            }
        }
    }
}

# ================================================================
# СПЕЦИАЛЬНЫЕ ФУНКЦИИ ДЛЯ СКАЧИВАНИЯ
# ================================================================

function Download-File-Advanced($url, $out, $desc) {
    Write-Host "  Downloading $desc..." -NoNewline
    
    $maxRetries = 3
    $retryDelay = 5
    
    for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
        try {
            if ($attempt -gt 1) {
                Write-Host "`n  Retry $attempt of $maxRetries..." -ForegroundColor Yellow
            }
            
            $webClient = New-Object System.Net.WebClient
            $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            $webClient.Headers.Add("Accept", "*/*")
            $webClient.Headers.Add("Accept-Language", "en-US,en;q=0.9")
            $webClient.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
            $webClient.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
            $webClient.DownloadFile($url, $out)
            $webClient.Dispose()
            
            $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
            Write-Host " done ($sizeMB MB)" -ForegroundColor Green
            return $true
            
        } catch {
            Write-Host "!" -NoNewline
            if ($attempt -lt $maxRetries) {
                Start-Sleep -Seconds $retryDelay
            } else {
                Write-Host " error" -ForegroundColor Red
                return $false
            }
        }
    }
    return $false
}

function Download-File-BITS($url, $out) {
    Write-Host "  Downloading via BITS..." -NoNewline
    try {
        $jobName = "Download_$(Get-Date -Format 'yyyyMMddHHmmss')"
        Start-BitsTransfer -Source $url -Destination $out -Priority High -DisplayName $jobName -ErrorAction Stop
        $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host " done ($sizeMB MB)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host " error" -ForegroundColor Red
        return $false
    }
}

function Download-File-NET($url, $out) {
    Write-Host "  Downloading via .NET HttpClient..." -NoNewline
    try {
        Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
        $handler = New-Object System.Net.Http.HttpClientHandler
        $handler.UseProxy = $true
        $handler.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
        $handler.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
        $handler.ServerCertificateCustomValidationCallback = { $true }
        
        $client = New-Object System.Net.Http.HttpClient($handler)
        $client.Timeout = [TimeSpan]::FromMinutes(5)
        $client.DefaultRequestHeaders.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        $response = $client.GetAsync($url).GetAwaiter().GetResult()
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $fileStream = [System.IO.File]::Create($out)
        $stream.CopyTo($fileStream)
        $fileStream.Close()
        $client.Dispose()
        
        $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host " done ($sizeMB MB)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host " error" -ForegroundColor Red
        return $false
    }
}

function Download-File-Curl($url, $out) {
    Write-Host "  Downloading via curl..." -NoNewline
    try {
        & curl -L -o $out $url --connect-timeout 30 --max-time 300 --ssl-no-revoke --insecure 2>$null
        if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) {
            $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
            Write-Host " done ($sizeMB MB)" -ForegroundColor Green
            return $true
        }
    } catch { }
    Write-Host " error" -ForegroundColor Red
    return $false
}

# ================================================================
# ОСНОВНОЙ БЛОК УСТАНОВКИ
# ================================================================

try {

Section "Assistant PB - Installation on Windows"
Write-Host "Project: $root"
Write-Host "Log: $log"
Write-Host "Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "PowerShell: $($PSVersionTable.PSVersion) Windows: $([Environment]::OSVersion.VersionString)"

# ================================================================
# ШАГ 1: УСТАНОВКА PYTHON 3.12 (СОВМЕСТИМАЯ ВЕРСИЯ ДЛЯ CHROMADB)
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
            Warn "winget failed: $($_.Exception.Message)"
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
# ШАГ 2: УСТАНОВКА OLLAMA + ЯЗЫКОВАЯ МОДЕЛЬ
# ================================================================

Section "2/8 Ollama + language model"

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $installer = Join-Path $env:TEMP "OllamaSetup.exe"
    
    if (Test-Path $installer) { Remove-Item $installer -Force -ErrorAction SilentlyContinue }
    
    $downloaded = $false
    $urls = @(
        "https://ollama.com/download/OllamaSetup.exe",
        "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe"
    )
    
    foreach ($url in $urls) {
        if ($downloaded) { break }
        Write-Host "  Trying URL: $url"
        
        if (-not $downloaded) {
            if (Download-File-Advanced $url $installer "Ollama Setup (WebClient)") {
                $downloaded = $true
            }
        }
        
        if (-not $downloaded) {
            if (Download-File-NET $url $installer) {
                $downloaded = $true
            }
        }
        
        if (-not $downloaded) {
            if (Download-File-BITS $url $installer) {
                $downloaded = $true
            }
        }
        
        if (-not $downloaded) {
            if (Download-File-Curl $url $installer) {
                $downloaded = $true
            }
        }
    }
    
    if (-not $downloaded) {
        Write-Host "  Final attempt: standard download..."
        try {
            Download-File "https://ollama.com/download/OllamaSetup.exe" $installer "Ollama Setup"
            $downloaded = $true
        } catch {
            Write-Host "  Final attempt failed" -ForegroundColor Yellow
        }
    }
    
    if (-not $downloaded) {
        Fail "Cannot download Ollama. Please check your internet connection and firewall.`nOr install manually: https://ollama.com/download/windows"
    }
    
    Write-Host "  Running Ollama installer..."
    Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait
    Refresh-Path
    Start-Sleep -Seconds 3
    
    if (-not (Test-Command "ollama")) { 
        Write-Host "  Trying installer with /VERYSILENT..." -ForegroundColor Yellow
        Start-Process -FilePath $installer -ArgumentList "/VERYSILENT" -Wait
        Refresh-Path
        Start-Sleep -Seconds 3
        if (-not (Test-Command "ollama")) { 
            Fail "Ollama did not install. Install manually: https://ollama.com/download/windows" 
        }
    }
    Ok "Ollama installed"
} else {
    Ok "Ollama already installed: $($ollamaCmd.Source)"
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
# ШАГ 3: УСТАНОВКА TESSERACT OCR + РУССКИЙ ЯЗЫК
# ================================================================


Section "3/8 Tesseract OCR + Russian language"

# Проверяем, установлен ли уже Tesseract
if (Test-Command "tesseract") {
    Ok "Tesseract already installed: $((Get-Command tesseract).Source)"
    
    # Проверяем русский язык
    $langs = (& tesseract --list-langs 2>&1) -join " "
    if ($langs -match "\brus\b") {
        Ok "Russian language: available"
    } else {
        Warn "Russian language pack not found"
        Write-Host "  Downloading Russian language pack..." -ForegroundColor Yellow
        try {
            $tessdataDir = "C:\Program Files\Tesseract-OCR\tessdata"
            if (-not (Test-Path $tessdataDir)) { 
                $tessdataDir = Join-Path (Split-Path (Get-Command tesseract).Source) "tessdata" 
            }
            $rusFile = Join-Path $tessdataDir "rus.traineddata"
            Download-File "https://github.com/tesseract-ocr/tessdata_best/raw/main/rus.traineddata" $rusFile "rus.traineddata"
            Ok "Russian language pack installed"
        } catch {
            Warn "Could not download Russian language pack"
            Write-Host "  You can download it manually from:" -ForegroundColor Yellow
            Write-Host "  https://github.com/tesseract-ocr/tessdata_best/raw/main/rus.traineddata" -ForegroundColor Cyan
            Write-Host "  And place it in: $tessdataDir" -ForegroundColor Cyan
        }
    }
    
} else {
    # Tesseract не установлен
    $installer = Join-Path $env:TEMP "tesseract-setup.exe"
    $localInstaller = Join-Path $root "installers\tesseract-ocr-w64-setup-5.3.3.20231005.exe"
    $downloaded = $false
    
    Write-Host "  Tesseract is not installed." -ForegroundColor Yellow
    
    # 1. ПРОВЕРЯЕМ ЛОКАЛЬНУЮ ПАПКУ installers
    if (Test-Path $localInstaller) {
        $sizeMB = [math]::Round((Get-Item $localInstaller).Length / 1MB, 1)
        Write-Host "  [LOCAL] Found tesseract-ocr-w64-setup-5.3.3.20231005.exe ($sizeMB MB)" -ForegroundColor Green
        Copy-Item $localInstaller $installer -Force
        $downloaded = $true
        
    } else {
        Write-Host "  [INFO] Tesseract installer not found in installers folder" -ForegroundColor Yellow
        Write-Host "  Attempting to download automatically..." -ForegroundColor Yellow
        
        $installAttempts = 0
        $maxInstallAttempts = 3
        
        # Список источников для скачивания
        $downloadMethods = @(
            @{
                Name = "GitHub (UB-Mannheim)"
                Url = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
            },
            @{
                Name = "GitHub (official)"
                Url = "https://github.com/tesseract-ocr/tesseract/releases/download/5.3.3/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
            },
            @{
                Name = "SourceForge mirror"
                Url = "https://sourceforge.net/projects/tesseract-ocr-alt/files/tesseract-ocr-w64-setup-5.3.3.20231005.exe/download"
            },
            @{
                Name = "UB-Mannheim mirror"
                Url = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
            }
        )
        
        while (-not $downloaded -and $installAttempts -lt $maxInstallAttempts) {
            $installAttempts++
            Write-Host "`n  Attempt $installAttempts of $maxInstallAttempts..." -ForegroundColor Cyan
            
            foreach ($method in $downloadMethods) {
                if ($downloaded) { break }
                
                Write-Host "    Trying: $($method.Name)..." -NoNewline
                
                $downloadSuccess = $false
                
                # Способ 1: WebClient
                if (-not $downloadSuccess) {
                    try {
                        $webClient = New-Object System.Net.WebClient
                        $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
                        $webClient.Headers.Add("Accept", "*/*")
                        $webClient.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
                        $webClient.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
                        $webClient.DownloadFile($method.Url, $installer)
                        $webClient.Dispose()
                        
                        if ((Test-Path $installer) -and ((Get-Item $installer).Length -gt 10MB)) {
                            $downloadSuccess = $true
                            Write-Host " SUCCESS!" -ForegroundColor Green
                        } else {
                            Remove-Item $installer -Force -ErrorAction SilentlyContinue
                        }
                    } catch {
                        # Пробуем следующий способ
                    }
                }
                
                # Способ 2: BITS
                if (-not $downloadSuccess) {
                    try {
                        Start-BitsTransfer -Source $method.Url -Destination $installer -Priority High -ErrorAction SilentlyContinue
                        if ((Test-Path $installer) -and ((Get-Item $installer).Length -gt 10MB)) {
                            $downloadSuccess = $true
                            Write-Host " SUCCESS!" -ForegroundColor Green
                        } else {
                            Remove-Item $installer -Force -ErrorAction SilentlyContinue
                        }
                    } catch {
                        # Пробуем следующий способ
                    }
                }
                
                # Способ 3: Invoke-WebRequest
                if (-not $downloadSuccess) {
                    try {
                        Invoke-WebRequest -Uri $method.Url -OutFile $installer -UseBasicParsing -TimeoutSec 120 -ErrorAction SilentlyContinue
                        if ((Test-Path $installer) -and ((Get-Item $installer).Length -gt 10MB)) {
                            $downloadSuccess = $true
                            Write-Host " SUCCESS!" -ForegroundColor Green
                        } else {
                            Remove-Item $installer -Force -ErrorAction SilentlyContinue
                        }
                    } catch {
                        # Пробуем следующий способ
                    }
                }
                
                if ($downloadSuccess) {
                    $downloaded = $true
                    $sizeMB = [math]::Round((Get-Item $installer).Length / 1MB, 1)
                    Write-Host "    Downloaded: $sizeMB MB" -ForegroundColor Green
                    
                    # Сохраняем копию в installers для будущего
                    if (-not (Test-Path (Join-Path $root "installers"))) {
                        New-Item -ItemType Directory -Path (Join-Path $root "installers") -Force | Out-Null
                    }
                    Copy-Item $installer $localInstaller -Force
                    Write-Host "  [OK] Saved copy to installers/ folder" -ForegroundColor Gray
                } else {
                    Write-Host " failed" -ForegroundColor Yellow
                }
            }
            
            if (-not $downloaded -and $installAttempts -lt $maxInstallAttempts) {
                Write-Host "  Waiting 5 seconds before retry..." -ForegroundColor Yellow
                Start-Sleep -Seconds 5
            }
        }
    }
    
    # 2. ЕСЛИ СКАЧАЛОСЬ - УСТАНАВЛИВАЕМ
    if ($downloaded -and (Test-Path $installer)) {
        $fileSize = (Get-Item $installer).Length
        if ($fileSize -gt 10MB) {
            Write-Host "`n  File exists: $([math]::Round($fileSize/1MB, 1)) MB" -ForegroundColor Green
            Write-Host "  Installing Tesseract..." -ForegroundColor Green
            Write-Host "  This may take a minute..." -ForegroundColor Yellow
            
            try {
                Start-Process -FilePath $installer -ArgumentList "/S" -Wait
                Refresh-Path
                Start-Sleep -Seconds 5
                
                # Проверяем установку
                if (-not (Test-Command "tesseract")) {
                    $tessPaths = @(
                        "C:\Program Files\Tesseract-OCR\tesseract.exe",
                        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
                    )
                    foreach ($path in $tessPaths) {
                        if (Test-Path $path) {
                            $dir = Split-Path $path
                            [Environment]::SetEnvironmentVariable('Path', "$env:Path;$dir", 'Machine')
                            $env:Path += ";$dir"
                            break
                        }
                    }
                }
                
                if (Test-Command "tesseract") { 
                    Ok "Tesseract installed successfully!" 
                    
                    # Скачиваем русский язык
                    Write-Host "  Downloading Russian language pack..." -ForegroundColor Yellow
                    try {
                        $tessdataDir = "C:\Program Files\Tesseract-OCR\tessdata"
                        if (-not (Test-Path $tessdataDir)) { 
                            $tessdataDir = Join-Path (Split-Path (Get-Command tesseract).Source) "tessdata" 
                        }
                        $rusFile = Join-Path $tessdataDir "rus.traineddata"
                        Download-File "https://github.com/tesseract-ocr/tessdata_best/raw/main/rus.traineddata" $rusFile "rus.traineddata"
                        Ok "Russian language pack installed"
                    } catch {
                        Warn "Could not download Russian language pack"
                        Write-Host "  You can download it manually from:" -ForegroundColor Yellow
                        Write-Host "  https://github.com/tesseract-ocr/tessdata_best/raw/main/rus.traineddata" -ForegroundColor Cyan
                    }
                } else {
                    Warn "Tesseract installer ran but command not found."
                    Write-Host "  Please restart your computer and run script again." -ForegroundColor Yellow
                }
                
            } catch {
                Warn "Installation failed: $($_.Exception.Message)"
                $downloaded = $false
            }
        } else {
            Write-Host "  File is too small ($([math]::Round($fileSize/1MB, 1)) MB). Download failed." -ForegroundColor Red
            $downloaded = $false
            Remove-Item $installer -Force -ErrorAction SilentlyContinue
        }
    }
    
    # 3. ЕСЛИ НЕ СКАЧАЛОСЬ - ИНСТРУКЦИЯ ДЛЯ ПОЛЬЗОВАТЕЛЯ
    if (-not $downloaded) {
        Write-Host ""
        Write-Host "="*70 -ForegroundColor Red
        Write-Host "  TESSERACT COULD NOT BE DOWNLOADED AUTOMATICALLY" -ForegroundColor Red
        Write-Host "="*70 -ForegroundColor Red
        Write-Host ""
        Write-Host "  Tesseract is required for OCR (text recognition in PDF scans)." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Please download and install it manually:" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  1. DOWNLOAD the installer from:" -ForegroundColor White
        Write-Host "     https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe" -ForegroundColor Green
        Write-Host ""
        Write-Host "  2. SAVE the file as:" -ForegroundColor White
        Write-Host "     $localInstaller" -ForegroundColor Green
        Write-Host ""
        Write-Host "  3. RUN the script again - it will use the local file" -ForegroundColor White
        Write-Host ""
        Write-Host "  4. If you want to install manually instead:" -ForegroundColor Cyan
        Write-Host "     - Run the downloaded file" -ForegroundColor Gray
        Write-Host "     - On 'Select Components' screen:" -ForegroundColor Gray
        Write-Host "       make sure 'Russian' language is CHECKED" -ForegroundColor Gray
        Write-Host "     - Complete the installation" -ForegroundColor Gray
        Write-Host "     - Restart your computer" -ForegroundColor Gray
        Write-Host ""
        Write-Host "="*70 -ForegroundColor Red
        
        Write-Host "`n  Opening browser with download page..." -ForegroundColor Yellow
        Start-Process "https://github.com/UB-Mannheim/tesseract/releases/tag/v5.3.3.20231005"
        Start-Sleep -Seconds 2
        
        Write-Host ""
        Write-Host "  Press ENTER after you have saved the file to installers folder" -ForegroundColor Green
        Write-Host "  Or type 'skip' to continue WITHOUT Tesseract" -ForegroundColor Yellow
        Write-Host ""
        
        $userInput = Read-Host "  [ENTER = continue, 'skip' = skip Tesseract]"
        
        if ($userInput -eq "skip" -or $userInput -eq "SKIP") {
            Warn "Tesseract installation skipped. OCR functionality will be limited."
            Warn "You can install Tesseract manually later."
        } else {
            # Проверяем, положил ли пользователь файл
            if (Test-Path $localInstaller) {
                Write-Host "  [OK] Tesseract installer found! Retrying installation..." -ForegroundColor Green
                Copy-Item $localInstaller $installer -Force
                
                Start-Process -FilePath $installer -ArgumentList "/S" -Wait
                Refresh-Path
                Start-Sleep -Seconds 5
                
                if (-not (Test-Command "tesseract")) {
                    $tessPaths = @(
                        "C:\Program Files\Tesseract-OCR\tesseract.exe",
                        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
                    )
                    foreach ($path in $tessPaths) {
                        if (Test-Path $path) {
                            $dir = Split-Path $path
                            [Environment]::SetEnvironmentVariable('Path', "$env:Path;$dir", 'Machine')
                            $env:Path += ";$dir"
                            break
                        }
                    }
                }
                
                if (Test-Command "tesseract") { 
                    Ok "Tesseract installed successfully!" 
                } else {
                    Warn "Tesseract installation failed. You may need to restart."
                }
            } else {
                Warn "Tesseract installer not found. Skipping."
            }
        }
    }
}

# ================================================================
# ШАГ 4: УСТАНОВКА POPPLER
# ================================================================

Section "4/8 Poppler"
$popplerDir = Join-Path $root "poppler"
if (Test-Path (Join-Path $popplerDir "Library\bin\pdftoppm.exe")) {
    Ok "Poppler already extracted to $popplerDir"
} else {
    $zip = Join-Path $env:TEMP "poppler.zip"
    Download-File "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip" $zip "Poppler 24.08"
    Write-Host "  Extracting Poppler..."
    Expand-Archive -Path $zip -DestinationPath $popplerDir -Force
    $inner = Get-ChildItem $popplerDir -Directory | Select-Object -First 1
    if ($inner -and (Test-Path (Join-Path $inner.FullName "Library\bin"))) {
        Get-ChildItem $inner.FullName | Move-Item -Destination $popplerDir -Force
        Remove-Item $inner.FullName -Recurse -Force
    }
    Ok "Poppler extracted"
}
$popplerBin = Join-Path $popplerDir "Library\bin"
if ($env:Path -notlike "*$popplerBin*") { $env:Path += ";$popplerBin" }

# ================================================================
# ШАГ 5: СОЗДАНИЕ VENV + УСТАНОВКА PYTHON-ЗАВИСИМОСТЕЙ
# ================================================================

Section "5/8 Python venv + dependencies"

$venv = Join-Path $root "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$venvPip = Join-Path $venv "Scripts\pip.exe"

# Удаляем старый venv если есть
if (Test-Path $venv) {
    Write-Host "  Removing old venv..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Создаём venv
Write-Host "  Creating venv with Python $(& $script:PYTHON --version)..." -ForegroundColor Yellow
& $script:PYTHON -m venv $venv

if (-not (Test-Path $venvPython)) { 
    Fail "Failed to create venv" 
}
Ok "venv created"

# Обновляем pip с обработкой ошибок
Write-Host "  Upgrading pip..." -ForegroundColor Yellow
try {
    & $venvPython -m pip install --upgrade pip --quiet --no-cache-dir
} catch {
    Write-Host "  Pip upgrade failed, continuing..." -ForegroundColor Yellow
}

# ================================================================
# УСТАНОВКА ПАКЕТОВ ПО ОДНОМУ
# ================================================================

Write-Host "`n  Installing required packages..." -ForegroundColor Yellow

# Проверяем, есть ли интернет
Write-Host "  Checking internet connection..." -NoNewline
try {
    $null = Invoke-WebRequest -Uri "https://pypi.org" -UseBasicParsing -TimeoutSec 5
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " FAILED" -ForegroundColor Red
    Warn "No internet connection. Please check your network."
}

# Список пакетов для установки
$packages = @(
    "numpy",
    "nltk",
    "chromadb",
    "langchain",
    "torch",
    "transformers",
    "sentence-transformers"
)

$installedOk = @()
$failedPackages = @()

foreach ($pkg in $packages) {
    Write-Host "  Installing $pkg..." -NoNewline
    
    # Пробуем установить
    $result = & $venvPython -m pip install $pkg --no-cache-dir --prefer-binary --timeout 120 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
        $installedOk += $pkg
    } else {
        Write-Host " FAILED" -ForegroundColor Red
        $failedPackages += $pkg
        
        # Повторная попытка
        Write-Host "    Retrying $pkg..." -NoNewline
        $result = & $venvPython -m pip install $pkg --no-cache-dir --timeout 180 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host " OK" -ForegroundColor Green
            $installedOk += $pkg
            $failedPackages = $failedPackages | Where-Object { $_ -ne $pkg }
        } else {
            Write-Host " FAILED" -ForegroundColor Red
        }
    }
}

# ================================================================
# УСТАНОВКА ПРОЕКТНЫХ ПАКЕТОВ
# ================================================================

Write-Host "`n  Installing project packages..." -ForegroundColor Yellow

$projectDirs = @(
    @{Name="apps\backend"; Path=Join-Path $root "apps\backend"},
    @{Name="apps\desktop"; Path=Join-Path $root "apps\desktop"},
    @{Name="packages\rag"; Path=Join-Path $root "packages\rag"}
)

foreach ($proj in $projectDirs) {
    $setupPath = Join-Path $proj.Path "setup.py"
    if (Test-Path $setupPath) {
        Write-Host "  Installing $($proj.Name)..." -NoNewline
        & $venvPython -m pip install -e $proj.Path --no-deps --no-cache-dir 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host " OK" -ForegroundColor Green
        } else {
            Write-Host " FAILED" -ForegroundColor Red
        }
    } else {
        Write-Host "  Skipping $($proj.Name) (no setup.py)" -ForegroundColor Gray
    }
}

# ================================================================
# ФИНАЛЬНАЯ ПРОВЕРКА
# ================================================================

Write-Host "`n  Checking installed packages..." -ForegroundColor Yellow

$checkPackages = @("numpy", "nltk", "chromadb", "langchain", "torch", "transformers")
$installed = & $venvPython -m pip list --format=freeze 2>$null
$allOk = $true
$missing = @()

foreach ($pkg in $checkPackages) {
    if ($installed -match "^$pkg==") {
        Write-Host "    $($pkg): OK" -ForegroundColor Green
    } else {
        Write-Host "    $($pkg): NOT FOUND" -ForegroundColor Red
        $allOk = $false
        $missing += $pkg
    }
}

# Если есть пропущенные - пытаемся установить
if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "  Missing packages: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "  Installing missing packages..." -ForegroundColor Yellow
    
    foreach ($pkg in $missing) {
        Write-Host "    Installing $pkg..." -NoNewline
        & $venvPython -m pip install $pkg --no-cache-dir --prefer-binary 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host " OK" -ForegroundColor Green
        } else {
            Write-Host " FAILED" -ForegroundColor Red
            Write-Host "      Try: pip install $pkg" -ForegroundColor Yellow
        }
    }
}

# Сохраняем список
$installedList = Join-Path $root "installed_packages.txt"
& $venvPython -m pip list > $installedList
Write-Host "`n  Installed packages list saved to: $installedList" -ForegroundColor Gray

if ($allOk) {
    Ok "All Python dependencies installed successfully!"
} else {
    Warn "Some packages are missing. You can install them manually:"
    Warn "  pip install $($missing -join ' ')"
}

Ok "Python dependencies installation completed"

# ================================================================
# ШАГ 6: ИНДЕКСАЦИЯ НОРМАТИВНОЙ БАЗЫ
# ================================================================

Section "6/8 Indexing regulatory database"
$chromaDir = Join-Path $root "data\chroma"
if ((Test-Path $chromaDir) -and (Get-ChildItem $chromaDir -File -Recurse | Measure-Object).Count -gt 0) {
    Ok "ChromaDB database already indexed"
} else {
    Write-Host "  Indexing corpus (first run will download embedding model ~1.3 GB)..."
    Push-Location $root
    try {
        $env:PYTHONPATH = "$root\apps\backend\src;$root\packages\rag\src"
        & $venvPython -m fire_safety_rag.indexer
        if ($LASTEXITCODE -ne 0) { Fail "Indexing error" }
    } finally {
        Pop-Location
    }
    Ok "Corpus indexed"
}

# ================================================================
# ШАГ 7: LANGUAGE TOOL (ОПЦИОНАЛЬНО)
# ================================================================

Section "7/8 LanguageTool (optional)"
$ltReady = $false
$ltSetup = Join-Path $root "tools\languagetool\setup.ps1"
if (Test-Path $ltSetup) {
    Push-Location $root
    try {
        & $ltSetup
        $ltReady = $true
        Ok "LanguageTool ready"
    } catch {
        Warn "Failed to install LanguageTool: $($_.Exception.Message) - application will work without it"
    } finally {
        Pop-Location
    }
} else {
    Warn "tools\languagetool\setup.ps1 not found - skipping"
}

# ================================================================
# ШАГ 8: СОЗДАНИЕ ЯРЛЫКА НА РАБОЧЕМ СТОЛЕ
# ================================================================

Section "8/8 Desktop shortcut"
$ltLaunchLine = ""
if ($ltReady) {
    $ltLaunchLine = 'start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\languagetool\start.ps1"'
}

$startBat = Join-Path $root "start.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0poppler\Library\bin;%PATH%"
set "PYTHONPATH=%~dp0apps\backend\src;%~dp0packages\rag\src;%~dp0apps\desktop\src"
if "%LLM_MODEL%"=="" set "LLM_MODEL=$model"
$ltLaunchLine
start "" "%~dp0venv\Scripts\pythonw.exe" -m fire_safety_desktop.main
endlocal
"@ | Set-Content -Path $startBat -Encoding ASCII
Ok "start.bat created"

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "Assistant PB.lnk"

$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($shortcut)
$lnk.TargetPath = $startBat
$lnk.WorkingDirectory = $root
$lnk.Description = "Fire Safety Assistant"
$lnk.Save()
Ok "Shortcut created: $shortcut"

# ================================================================
# ЗАВЕРШЕНИЕ УСТАНОВКИ
# ================================================================

Section "Done"
Write-Host ""
Write-Host "All installed." -ForegroundColor Green
Write-Host "Launch: double-click the 'Assistant PB' shortcut on the desktop."
Write-Host "Or: $startBat"
Write-Host ""
Write-Host "Test samples: $root\tests\samples\"
Write-Host "Installation log: $log"
Write-Host ""

try { Stop-Transcript | Out-Null } catch { }
Write-Host "Press Enter to close..."
try { [Console]::ReadLine() | Out-Null } catch { Read-Host }

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
    Write-Host "Installation log: $log" -ForegroundColor Cyan
    Write-Host "Please send this log + screenshot to the developer." -ForegroundColor Cyan
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host "Press Enter to close..."
    try { [Console]::ReadLine() | Out-Null } catch { Read-Host }
    exit 1
}
