# Как вкладываться в проект

## Установка окружения (dev)

```bash
# Требуется Python 3.11–3.13 и uv
uv sync --all-packages --dev
```

Или без uv — через venv + pip:

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e apps/backend -e apps/desktop -e packages/rag
pip install -r <(python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['dependency-groups']['dev']))")
```

## Структура репозитория

```
apps/
  backend/          FastAPI-приложение (MVC)
  desktop/          pywebview-обёртка + фронтенд
packages/
  rag/              Переиспользуемый RAG-модуль по нормативной базе
docs/               Документация по 7 разделам (см. docs/README.md)
install/            Скрипты установки (Windows, macOS)
scripts/            Сборка приложения и утилиты
tests/samples/      Демо-документы для ручного тестирования
```

## Команды

| Что | Как |
|---|---|
| Запустить backend локально | `PYTHONPATH=apps/backend/src:packages/rag/src uvicorn fire_safety_backend.main:app --reload` |
| Запустить desktop | `PYTHONPATH=apps/backend/src:packages/rag/src:apps/desktop/src python -m fire_safety_desktop.main` |
| Проиндексировать корпус | `PYTHONPATH=packages/rag/src python -m fire_safety_rag.indexer` |
| Тесты | `PYTHONPATH=apps/backend/src:packages/rag/src pytest -q` |
| Lint | `ruff check .` |
| Format | `ruff format .` |
| Types | `mypy apps/backend/src packages/rag/src` |

## Правила по коммитам

Используем conventional commits:

- `feat: добавить X` — новая функциональность
- `fix: починить Y` — исправление бага
- `docs: обновить README` — только документация
- `refactor: вынести Z в отдельный модуль` — без изменения поведения
- `test: покрыть W` — только тесты
- `chore: обновить зависимости` — инфраструктура

## Pull Request

- Тесты и линтеры должны быть зелёными (`pytest`, `ruff check`, `ruff format --check`).
- В `CHANGELOG.md` добавьте пункт в раздел `## [Unreleased]`.
- Если меняется публичный API — обновите `docs/03-architecture/`.

## Что тестировать вручную

Полный смоук на реальном LLM (нужна поднятая Ollama и проиндексированная
база) описан в [`docs/05-quality/test-strategy.md`](docs/05-quality/test-strategy.md).
Три сценария из `tests/samples/`:
- `spellcheck_bad.txt` — проверка орфографии,
- `contract_short.txt` — юр. анализ,
- `letter_draft.txt` — генерация письма.
