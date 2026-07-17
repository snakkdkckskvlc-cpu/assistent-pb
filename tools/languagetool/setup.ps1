# ================================================================
# LanguageTool — скачивание портативного JDK 17 + релиза LanguageTool
# ================================================================
# Windows-аналог setup.sh. Ничего не устанавливает в систему — всё
# распаковывается в этот же каталог (tools\languagetool\). Скачивает
# один раз, идемпотентно (пропускает уже скачанное). ~430 МБ, нужен
# интернет один раз.
#
# Обычно вызывается автоматически из bootstrap.ps1 (шаг "LanguageTool"),
# но можно запустить отдельно: .\tools\languagetool\setup.ps1
# ================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($PSScriptRoot) { $root = $PSScriptRoot } else { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "  $msg" }

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

# --- JDK 17 (Eclipse Temurin, портативный zip, x64) ---
$jdkDir = Get-ChildItem -Path $root -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jdkDir) {
    Ok "JDK уже есть: $($jdkDir.Name)"
} else {
    Info "Скачиваю портативный JDK 17 (Temurin, windows x64)..."
    $jdkZip = Join-Path $root "jdk.zip"
    Download-File "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse" $jdkZip "JDK 17"
    Info "Распаковываю JDK..."
    Expand-Archive -Path $jdkZip -DestinationPath $root -Force
    Remove-Item $jdkZip -Force
    $jdkDir = Get-ChildItem -Path $root -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $jdkDir) { throw "JDK распаковался, но папка jdk-* не найдена" }
    Ok "Готово: $($jdkDir.Name)"
}

# --- LanguageTool (релиз, содержит languagetool-server.jar) ---
$ltDir = Join-Path $root "LanguageTool-6.6"
if (Test-Path $ltDir) {
    Ok "LanguageTool уже есть: LanguageTool-6.6"
} else {
    Info "Скачиваю LanguageTool (~240 МБ)..."
    $ltZip = Join-Path $root "lt.zip"
    Download-File "https://languagetool.org/download/LanguageTool-stable.zip" $ltZip "LanguageTool"
    Info "Распаковываю LanguageTool..."
    Expand-Archive -Path $ltZip -DestinationPath $root -Force
    Remove-Item $ltZip -Force
    if (-not (Test-Path $ltDir)) {
        # Если апстрим сменил версию — найдём фактическую папку и подскажем,
        # что start.ps1 и bootstrap.ps1 ссылаются на конкретную "LanguageTool-6.6".
        $actual = Get-ChildItem -Path $root -Directory -Filter "LanguageTool-*" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($actual) {
            throw "Распаковалась папка '$($actual.Name)', а не 'LanguageTool-6.6' — обновите версию в tools\languagetool\setup.ps1 и start.ps1"
        }
        throw "LanguageTool распаковался, но папка LanguageTool-6.6 не найдена"
    }
    Ok "Готово: LanguageTool-6.6"
}

Write-Host ""
Write-Host "Установка LanguageTool завершена. Запуск: .\tools\languagetool\start.ps1" -ForegroundColor Green
