# ADR-0002 · MVC-разбиение backend

**Дата**: 2026-07-14 · **Статус**: принято

## Контекст

В Sprint 1 весь backend был одним файлом `main.py` на ~180 строк: роуты,
парсинг входа, вызовы пайплайнов, статика. Пайплайны, парсеры и очередь
жили в соседних файлах, но границы между слоями были размыты.

## Решение

Разбиваем backend по MVC-подобному layout'у:

```
apps/backend/src/fire_safety_backend/
├── main.py           # app factory
├── config.py         # env-настройки
├── models/           # M — Pydantic-схемы
├── views/            # V — HTTP-роутеры (тонкие)
├── controllers/      # (зарезервировано под сложную оркестрацию)
├── services/         # Бизнес-логика (upload → text)
├── pipelines/        # Три пайплайна (spellcheck / legal / letter)
├── infrastructure/   # LLM, очередь, парсеры, генераторы
└── resources/        # промпты + DOCX-шаблоны
```

**Правила границ**:
- `views/` могут звать `services/` и `pipelines/`, но не `infrastructure/` напрямую.
- `services/` — синхронная бизнес-логика без побочных эффектов сети.
- `pipelines/` — асинхронные многошаговые сценарии (промпт + LLM + RAG).
- `infrastructure/` — обёртки над внешними системами (Ollama, ChromaDB,
  Tesseract).

## Альтернативы

1. **Оставить монолитный `main.py`** — простой, но плохо масштабируется.
2. **Hexagonal / Clean Architecture** (ports & adapters) — правильнее, но
   для MVP на 4 роута — overkill.
3. **DDD с полным разделением на bounded contexts** — правильно на длинную
   дистанцию, отложено до Sprint 5+, когда добавятся новые функции.

## Последствия

**Плюсы**:
- Каждый роутер — один файл, легко искать.
- Легко замокать `services/` и `pipelines/` в тестах.
- Понятно, куда добавить новую фичу.

**Минусы**:
- Небольшой overhead: 8 файлов там, где раньше был 1.
- Слои `controllers/` пока избыточны — оставили как задел на будущее.

## Ссылки

- [C4 · Container](../c4-container.md)
- [DDD · Bounded Contexts](../ddd-bounded-contexts.md)
