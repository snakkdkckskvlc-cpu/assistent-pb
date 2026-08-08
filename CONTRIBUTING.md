# Как вкладываться в проект

## Установка окружения (dev)

Версия Python зависит от машины: на боевом сервере 3.12, на сборке 3.11, на
ноутбуке разработчика 3.13. Пакеты допускают `>=3.11,<3.14` — годится любая из
трёх. Разбор, почему их три, — в `CLAUDE.md` §3.

```bash
# Так собирается на GitHub. Требуется uv.
uv sync --all-packages --dev
```

**На ноутбуке автора `uv` не установлен** — там второй способ, и все команды
зовутся как `./venv/bin/python …`. Это не спор двух инструкций: первый способ
для сборки, второй для машины, где идёт работа.

Через venv + pip:

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
| Тесты — **полный прогон только на сборке и сервере** | `PYTHONPATH=apps/backend/src:packages/rag/src pytest -q` |
| Тесты на ноутбуке автора — точечно, по тронутым файлам | `./venv/bin/python -m pytest apps/backend/tests/unit/test_<модуль>.py -q` |
| Все шесть шагов сборки разом, на чистой копии, до отправки | `./venv/bin/python .claude/hooks/preflight.py` |
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
  Гоняет их сборка на GitHub — ноутбук автора полный прогон не тянет, это его
  прямое указание. Перед отправкой запускать `.claude/hooks/preflight.py`: он
  делает ровно те же шаги на чистой копии.
- В `CHANGELOG.md` добавьте пункт в раздел `## [Unreleased]`.
- Если меняется публичный API — обновите `docs/03-architecture/`.

## Что тестировать вручную

Полный смоук на реальном LLM (нужна поднятая Ollama и проиндексированная
база) описан в [`docs/05-quality/test-strategy.md`](docs/05-quality/test-strategy.md).
Три сценария из `tests/samples/`:
- `spellcheck_bad.txt` — проверка орфографии,
- `contract_short.txt` — юр. анализ,
- `letter_draft.txt` — генерация письма.
