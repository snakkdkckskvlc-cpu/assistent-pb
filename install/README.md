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
7. LanguageTool — офлайн-проверка орфографии в дополнение к LLM (опционально, портативный JDK + сервер, ничего не ставится в систему; см. `tools/languagetool/`). Если шаг не пройдёт (нет сети) — установка не останавливается, просто эта функция будет недоступна.
8. Ярлык «Ассистент ПБ» на рабочем столе с иконкой.

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
7. **LanguageTool** (опционально — офлайн-проверка орфографии в дополнение к LLM):
   ```powershell
   .\tools\languagetool\setup.ps1   # один раз, скачивает JDK + LanguageTool, ~430 МБ
   .\tools\languagetool\start.ps1   # держать запущенным рядом с Ollama
   ```
7a. **Архив писем компании** (опционально — генерация писем начнёт опираться на
   реальный стиль компании, а не только на промпт). Архив с письмами в git не
   попадает (коммерческие данные) — путь укажите свой:
   ```powershell
   $env:PYTHONPATH = "apps\backend\src;packages\rag\src"
   .\venv\Scripts\python scripts\index_letters.py --zip "D:\Архив\письма.zip"
   ```
   Берутся только DOCX из подпапки «Письма» (настраивается флагом `--folder`).
   Повторный запуск того же архива ничего не дублирует.
8. **Фирменный бланк** — ничего делать не нужно, `letterhead.docx` приезжает
   вместе с кодом (репозиторий приватный). Пересобирать его нужно ТОЛЬКО если
   меняется сам бланк компании:
   ```powershell
   .\venv\Scripts\python scripts\build_letterhead_template.py --source "<путь к бланку>.docx"
   ```
   Запускать этот шаг «на всякий случай» не надо: результат перезаписывает
   поставляемый шаблон, дерево становится изменённым, и автообновление на этой
   машине молча перестаёт работать (см. updater._is_clean_main).
9. **Запуск**:
   ```powershell
   $env:PYTHONPATH = "apps\backend\src;packages\rag\src;apps\desktop\src"
   .\venv\Scripts\python -m fire_safety_desktop.main
   ```
10. **Сборка `.exe` с иконкой** (опционально):
    ```powershell
    .\scripts\build_windows_app.ps1
    ```
    Результат: `dist\АссистентПБ\АссистентПБ.exe`.

## macOS / Linux

См. [`docs/07-ops/install-macos.md`](../docs/07-ops/install-macos.md).

## После установки

**Интернет больше не нужен** — система работает офлайн.
