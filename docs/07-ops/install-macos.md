# Установка на macOS / Linux (для разработчика)

## Зависимости

- **Python 3.11–3.13** (не 3.14 — `pydantic-core` не собирается).
- **Ollama**: `brew install ollama` (macOS) / `curl -fsSL https://ollama.com/install.sh | sh` (Linux).
- **Tesseract** (для OCR сканов): `brew install tesseract tesseract-lang` (macOS).
- **Poppler** (для pdf2image): `brew install poppler` (macOS).

## Установка проекта

```bash
git clone <repo-url> fire-safety-assistant
cd fire-safety-assistant

python3.13 -m venv venv
venv/bin/pip install -e apps/backend -e apps/desktop -e packages/rag
# либо: uv sync --all-packages --dev
```

## Модель Ollama

```bash
ollama serve   # (macOS: запускается автоматически как daemon)
ollama pull qwen2.5:7b-instruct   # ~4.7 ГБ
```

## Индексация нормативной базы

При первом запуске скачает эмбед-модель `intfloat/multilingual-e5-large`
(~1.3 ГБ):

```bash
PYTHONPATH=packages/rag/src venv/bin/python -m fire_safety_rag.indexer
```

## Запуск

### Только backend (dev-режим с автоперезагрузкой)

```bash
PYTHONPATH=apps/backend/src:packages/rag/src \
  venv/bin/uvicorn fire_safety_backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Открыть <http://127.0.0.1:8000/>.

### Backend + нативное окно

```bash
PYTHONPATH=apps/backend/src:packages/rag/src:apps/desktop/src \
  venv/bin/python -m fire_safety_desktop.main
```

### Сборка `.app` для macOS

```bash
scripts/build_macos_app.sh
open "build/Ассистент ПБ.app"
```

## Переменные окружения

Скопировать `.env.example` в `.env` и подправить (для боевого сервера
поставить `LLM_MODEL=qwen2.5:14b-instruct-q4_K_M`).

## Тесты

```bash
PYTHONPATH=apps/backend/src:packages/rag/src venv/bin/python -m pytest -q
```

Реальный smoke на LLM: положить документы в `tests/samples/` (уже есть три
демо) → нажать соответствующую кнопку в UI.
