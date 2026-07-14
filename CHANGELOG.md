# Changelog

Все значимые изменения проекта фиксируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует [семантическому версионированию](https://semver.org/lang/ru/).

## [0.2.0] — 2026-07-14 · Sprint 2. Реорганизация и документация

### Изменено
- **Полностью новая структура репозитория**: monorepo `apps/` + `packages/` +
  `docs/`. Backend разбит по MVC (`models/`, `views/`, `controllers/`,
  `services/`, `infrastructure/`, `pipelines/`).
- RAG вынесен в самостоятельный пакет `packages/rag` (`fire_safety_rag`),
  теперь не зависит от backend'а и переиспользуем.
- Frontend и pywebview-обёртка переехали в `apps/desktop/`.
- Единый `pyproject.toml` через uv workspaces + тонкие манифесты в пакетах.

### Добавлено
- Документация в `docs/` по разделам эталонного вузовского IT-проекта:
  vision (Elevator Pitch, Lean Canvas), product (User Stories, RICE),
  architecture (C4, DDD, ER, ADR), design (wireframes), quality (BDD,
  риски), team (Hiring Plan, Team Rituals), ops (установка на Windows/macOS).
- Smoke-тесты (`pytest`) для `/api/health` и трёх пайплайнов с моком LLM.
- Юнит-тесты чанкера RAG.
- CI GitHub Actions: `ruff check`, `ruff format --check`, `mypy`, `pytest`.
- `LICENSE` (MIT), `CHANGELOG.md`, `CONTRIBUTING.md`, `.editorconfig`,
  `.env.example`, `ruff.toml`, `mypy.ini`.

### Исправлено
- Клик по подписи «Загрузить файл» теперь открывает диалог выбора файла
  (`<label for="file">`, скрытый `<input>`, кастомная кнопка `.btn`).
- Убран таймер обработки — прогресс показывается только спиннером.
- `TaskQueue` создаёт `asyncio.Queue` в `start()`, а не при импорте — иначе
  не работала в многократных event loop'ах (в частности в тестах).
- Замена deprecated `datetime.utcnow()` на `datetime.now(timezone.utc)`.

## [0.1.0] — 2026-07-10 · Sprint 1. MVP

### Добавлено
- Три функции: проверка орфографии/пунктуации/стиля, юр. анализ договоров
  с RAG-подсказками по ФЗ/ГК РФ, генерация официальных писем на бланке.
- Backend на FastAPI + очередь на один воркер.
- Локальный LLM через Ollama (qwen2.5:7b-instruct для тестов,
  qwen2.5:14b-instruct-q4_K_M для боевого сервера).
- RAG на ChromaDB + `multilingual-e5-large`, корпус: ФЗ-69, 123-ФЗ,
  ППР 1479, ГК РФ ч.1-2, СП 3.13130/4.13130, КоАП 20.4.
- Парсеры DOCX / PDF / OCR (Tesseract + Poppler).
- Frontend — SPA-ish (HTML + vanilla JS + CSS).
- pywebview-обёртка с иконкой и `.app` бандлом для macOS.
- `bootstrap.ps1` — автоматическая установка на Windows.
