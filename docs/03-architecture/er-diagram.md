# ER-диаграмма модели данных

**Замечание**: MVP не использует реляционную БД — все объекты живут в
памяти (in-memory очередь задач). Диаграмма показывает **логическую**
модель, которая пригодится при переходе на постоянное хранилище (Sprint 5+).

```mermaid
erDiagram
    TASK ||--o{ SPELLCHECK_RESULT : produces
    TASK ||--o{ LEGAL_RESULT : produces
    TASK ||--o{ LETTER_RESULT : produces

    SPELLCHECK_RESULT ||--o{ ISSUE : contains
    LEGAL_RESULT ||--o{ FINDING : contains
    LEGAL_RESULT ||--o{ RAG_SOURCE : cites
    FINDING }o--|| NORM : refers_to

    LETTER_RESULT ||--o| DOCX_FILE : generates
    LETTER_RESULT }o--|| LETTERHEAD : uses

    NORM ||--o{ RAG_CHUNK : chunked_into
    RAG_CHUNK }o--|| DOCUMENT_SOURCE : from

    TASK {
        string id PK
        string kind "spellcheck|legal|letter"
        string status "queued|running|done|error"
        string progress
        datetime created_at
        datetime started_at
        datetime finished_at
        json result
        string error
    }

    ISSUE {
        string type "орфография|пунктуация|стиль"
        text before
        text after
        text reason
        int chunk_idx
    }

    FINDING {
        string критичность "красный|жёлтый|зелёный"
        text цитата_из_договора
        text в_чём_риск
        string ссылка_на_норму
        text предложение_правки
    }

    RAG_SOURCE {
        string filename
    }

    NORM {
        string название "ФЗ-69 / 123-ФЗ / ППР 1479 / ГК РФ ..."
        string статья
    }

    RAG_CHUNK {
        string id PK
        text content
        int chunk_idx
        string file_hash
        vector embedding
    }

    DOCUMENT_SOURCE {
        string filename
        string kind "law|standard|regulation"
    }

    LETTER_RESULT {
        string тема
        string обращение
        text тело
        string формула_вежливости
        string должность_отправителя
        string фио_отправителя
    }

    DOCX_FILE {
        string filename
        int size_bytes
        datetime created_at
    }

    LETTERHEAD {
        string filename
        map placeholders
    }
```

## Текущая реализация (in-memory)

- **`TASK`** — dict в `TaskQueue._tasks`.
- **`RAG_CHUNK`** — коллекция в ChromaDB.
- **`DOCX_FILE`** — файлы в `data/outputs/`.
- **`ISSUE`, `FINDING`, `LETTER_RESULT`** — Python-словари в `Task.result`.

## Переход на постоянное хранение (после MVP)

Когда появится потребность хранить историю задач между запусками:
- **`TASK`, `ISSUE`, `FINDING`, `LETTER_RESULT`** → PostgreSQL/SQLite (via SQLAlchemy).
- **`DOCX_FILE`** → S3-совместимое хранилище (MinIO локально).
- **`RAG_CHUNK`** → остаётся в ChromaDB (можно перейти на Qdrant для кластера).
