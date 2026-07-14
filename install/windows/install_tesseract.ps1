# Установка Tesseract OCR с русским языковым пакетом.
# Запускать с правами администратора.

$ErrorActionPreference = "Stop"

Write-Host "=== Fire Safety Assistant: установка Tesseract OCR ===" -ForegroundColor Cyan

$tesseractCmd = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tesseractCmd) {
    Write-Host "Tesseract уже установлен: $($tesseractCmd.Source)" -ForegroundColor Green
} else {
    Write-Host "Скачиваем установщик Tesseract 5.x…"
    $installerUrl = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
    $installer = "$env:TEMP\tesseract-setup.exe"
    Invoke-WebRequest -Uri $installerUrl -OutFile $installer
    Write-Host "Запускаем установщик. ВАЖНО: в 'Additional language data' поставьте галочку на 'Russian'."
    Start-Process -FilePath $installer -Wait
    # Обновить PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Проверка русского языка
Write-Host "Проверяем языковые пакеты…"
$langs = & tesseract --list-langs 2>&1
if ($langs -notmatch "rus") {
    Write-Host "ВНИМАНИЕ: русский языковой пакет не установлен." -ForegroundColor Yellow
    Write-Host "Скачайте rus.traineddata с https://github.com/tesseract-ocr/tessdata_best" -ForegroundColor Yellow
    Write-Host "и положите его в: C:\Program Files\Tesseract-OCR\tessdata\"
} else {
    Write-Host "Русский язык доступен." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Готово ===" -ForegroundColor Green
Write-Host "Не забудьте установить Poppler для pdf2image:"
Write-Host "  https://github.com/oschwartz10612/poppler-windows/releases"
Write-Host "  и добавить bin/ в PATH."
