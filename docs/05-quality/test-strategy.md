# Стратегия тестирования

## Пирамида

```
        ▲
       /E\        E2E — ручные тесты по BDD-сценариям на реальном LLM
      /---\
     / API \     Smoke — pytest + TestClient, LLM/RAG замоканы
    /-------\
   /  Unit   \   Юнит-тесты чистых функций (чанкер, парсеры)
  /-----------\
```

## Автоматизация

### Юнит

- **Где**: `apps/backend/tests/unit/`, `packages/rag/tests/`.
- **Что покрываем**: чанкер RAG, парсеры DOCX/PDF (мокая I/O), утилиты
  чтения промптов.
- **Быстро**: < 1 сек на весь набор.
- **Пример**: `test_chunker.py` — три теста на функцию `_chunk_text`.

### Smoke (интеграционные с моками)

- **Где**: `apps/backend/tests/smoke/`.
- **Что покрываем**: HTTP-эндпоинты, очередь задач, парсинг входа.
- **Как**: `FastAPI TestClient` + `monkeypatch` для `llm.chat_json`,
  `retrieve`, `build_letter_docx`.
- **Быстро**: < 5 сек на весь набор.
- **Пример**: `test_endpoints_up.py` — три сценария (spellcheck, legal,
  letter) + отказ на пустом входе.

### E2E — ручные с реальным LLM

- **Где**: `tests/samples/*.txt` + чек-лист в
  [BDD-сценариях](bdd-scenarios.feature).
- **Когда**: перед релизом, при обновлении промптов или модели.
- **Что покрываем**: качество ответов LLM, работоспособность OCR,
  корректность RAG-подсказок.
- **Долго**: 15–40 минут на все три сценария (реальный LLM на CPU).

## CI

`.github/workflows/ci.yml` при каждом PR:

1. `uv sync --all-packages --dev`
2. `ruff check .` — линтинг.
3. `ruff format --check .` — форматирование.
4. `mypy apps/backend/src packages/rag/src` — типы (non-blocking пока).
5. `pytest -q` — unit + smoke.

Реальные вызовы Ollama в CI **не делаем** — только моки. Причина: агент
CI не имеет GPU, LLM тянет 5+ ГБ RAM, а прогон одного сценария — минуты.

## Матрица покрытия (текущее состояние)

| Модуль | Unit | Smoke | E2E |
|---|:-:|:-:|:-:|
| `views/health.py` | — | ✅ | ✅ |
| `views/spellcheck.py` | — | ✅ | ✅ |
| `views/legal.py` | — | ✅ | ✅ |
| `views/letter.py` | — | ✅ | ✅ |
| `views/tasks.py` | — | ✅ (косвенно) | ✅ |
| `views/downloads.py` | — | ❌ | ✅ |
| `services/uploads.py` | ❌ | ✅ (косвенно) | ✅ |
| `infrastructure/queue.py` | ❌ | ✅ (косвенно) | ✅ |
| `infrastructure/llm.py` | ❌ | 🟨 замокан | ✅ |
| `infrastructure/parsers/*` | ❌ | ❌ | ✅ |
| `infrastructure/generators/letter_docx.py` | ❌ | 🟨 замокан | ✅ |
| `pipelines/legacy.py::run_spellcheck` | ❌ | ✅ (частично) | ✅ |
| `pipelines/legacy.py::run_legal_analysis` | ❌ | ✅ (частично) | ✅ |
| `pipelines/legacy.py::run_letter` | ❌ | ✅ (частично) | ✅ |
| `fire_safety_rag.indexer::_chunk_text` | ✅ | — | ✅ |
| `fire_safety_rag.retriever` | ❌ | ❌ | ✅ |

## Ближайшие цели

- Добавить unit-тесты парсеров (DOCX/PDF на маленьких фикстурах).
- Добавить unit-тесты для `build_letter_docx` (проверить, что плейсхолдеры
  заменяются, без реального шаблона).
- Настроить `mypy` в strict-режиме для новых модулей.
