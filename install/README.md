# Установка на сервере компании

Скрипты для автоматической установки лежат в:

- **`install/windows/`** — PowerShell-скрипты (`install_ollama.ps1`, `install_tesseract.ps1`)
- **`install/macos/`**, **`install/linux/`** — планируется в Sprint 3

## Windows — полностью автоматически (рекомендуется)

Все шаги выполняет один PowerShell-скрипт из корня проекта:

```powershell
.\START.bat
```

Что он делает:
1. Python 3.11+ (через winget или python.org).
2. Ollama + `qwen2.5:7b-instruct` (~4.7 ГБ).
3. Tesseract с русским языком.
4. Poppler (для OCR PDF).
5. `venv` + Python-зависимости из `apps/backend/pyproject.toml`, `apps/desktop/pyproject.toml`, `packages/rag/pyproject.toml`.
6. Индексация корпуса `packages/rag/corpus/`.
7. Ярлык «Ассистент ПБ» на рабочем столе с иконкой.

Полная инструкция для тестировщика — [`docs/07-ops/install-windows.md`](../docs/07-ops/install-windows.md).

## Windows — вручную (если автоскрипт не сработал)

1. **Python 3.11–3.13**: https://www.python.org/downloads/windows/ (галочка «Add to PATH»).
2. **Ollama**: `.\install\windows\install_ollama.ps1`.
3. **Tesseract**: `.\install\windows\install_tesseract.ps1`.
4. **Poppler**: https://github.com/oschwartz10612/poppler-windows/releases → распаковать в `C:\Program Files\poppler` → добавить `bin` в PATH.
5. **venv + зависимости**:
   ```powershell
   python -m venv venv
   .\venv\Scripts\pip install -e apps\backend -e apps\desktop -e packages\rag
   ```
6. **Индексация**:
   ```powershell
   $env:PYTHONPATH = "apps\backend\src;packages\rag\src"
   .\venv\Scripts\python -m fire_safety_rag.indexer
   ```
7. **Фирменный бланк** — положить `letterhead.docx` в
   `apps\backend\src\fire_safety_backend\resources\templates\`. Плейсхолдеры:
   `{{date}}`, `{{recipient}}`, `{{subject}}`, `{{greeting}}`, `{{body}}`,
   `{{signoff}}`, `{{sender_position}}`, `{{sender_name}}`.
8. **Запуск**:
   ```powershell
   $env:PYTHONPATH = "apps\backend\src;packages\rag\src;apps\desktop\src"
   .\venv\Scripts\python -m fire_safety_desktop.main
   ```
9. **Сборка `.exe` с иконкой** (опционально):
   ```powershell
   .\scripts\build_windows_app.ps1
   ```
   Результат: `dist\АссистентПБ\АссистентПБ.exe`.

## macOS / Linux

См. [`docs/07-ops/install-macos.md`](../docs/07-ops/install-macos.md).

## После установки

**Интернет больше не нужен** — система работает офлайн.
