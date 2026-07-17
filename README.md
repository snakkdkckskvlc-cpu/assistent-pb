# 🔥 Ассистент ПБ

**Локальный ИИ-ассистент для компании в сфере пожарной безопасности.**
Работает **полностью офлайн** на сервере компании. Три функции:

1. **Проверка документа** — орфография, пунктуация, деловой стиль.
2. **Юридический анализ договора** — риски с цветовой маркировкой, ссылки
   на ФЗ и ГК РФ, предложения правок.
3. **Официальное письмо** — превращает набросок в письмо на фирменном бланке
   по ГОСТ Р 7.0.97-2016.

---

## Оглавление

- [Быстрый старт (Windows, тестировщик)](docs/07-ops/install-windows.md)
- [Разработчику (macOS/Linux)](CONTRIBUTING.md)
- [Полная документация](docs/README.md) — 7 разделов: vision · product · architecture · design · quality · team · ops

## Структура репозитория

```
apps/
  backend/          FastAPI-приложение (MVC: models/views/controllers/services)
  desktop/          pywebview-обёртка + фронтенд (HTML/CSS/JS)
packages/
  rag/              RAG по нормативной базе РФ (ChromaDB + multilingual-e5-large)
docs/               7 разделов проектной документации
install/            Скрипты установки (Windows / macOS / Linux)
scripts/            Сборка приложения, генерация иконок
tests/samples/      Демо-документы для ручного тестирования
```

## Быстрый старт (dev, macOS/Linux)

```bash
python3.13 -m venv venv
venv/bin/pip install -e apps/backend -e apps/desktop -e packages/rag  # или: uv sync --all-packages --dev

# Индексация корпуса законов (первый раз тянет эмбед-модель ~1.3 ГБ)
PYTHONPATH=packages/rag/src venv/bin/python -m fire_safety_rag.indexer

# Backend
PYTHONPATH=apps/backend/src:packages/rag/src \
  venv/bin/uvicorn fire_safety_backend.main:app --host 127.0.0.1 --port 8000

# Desktop-окно (pywebview)
PYTHONPATH=apps/backend/src:packages/rag/src:apps/desktop/src \
  venv/bin/python -m fire_safety_desktop.main
```

Открыть в браузере: <http://127.0.0.1:8000/>.

## Быстрый старт (Windows, продакшн-тест)

Полностью автоматический установщик:

```powershell
# Распаковать архив, из папки проекта:
.\START.bat
```

Скрипт поставит Python, Ollama, Tesseract, Poppler, зависимости, проиндексирует
корпус и создаст ярлык «Ассистент ПБ» на рабочем столе. Подробно — в
[`docs/07-ops/install-windows.md`](docs/07-ops/install-windows.md).

## Стек

- **Python 3.11–3.13**, FastAPI, Pydantic 2, uvicorn.
- **LLM**: Ollama · `qwen2.5:7b-instruct` (dev) или `qwen2.5:14b-instruct-q4_K_M` (prod).
- **RAG**: ChromaDB (embedded) + sentence-transformers `intfloat/multilingual-e5-large`.
- **Парсинг**: python-docx, pdfplumber, Tesseract (OCR), Poppler.
- **Desktop**: pywebview (Chromium/WebKit).
- **Качество**: ruff, mypy, pytest, GitHub Actions.

## Лицензия

MIT — см. [LICENSE](LICENSE).

## Ссылки

- 📄 [CHANGELOG](CHANGELOG.md)
- 🤝 [CONTRIBUTING](CONTRIBUTING.md)
- 📚 [Полная документация](docs/README.md)
