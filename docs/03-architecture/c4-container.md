# C4 · Уровень 2. Контейнеры

Показывает основные компоненты (контейнеры) системы «Ассистент ПБ» и как
они общаются.

```mermaid
C4Container
    title Контейнеры «Ассистент ПБ»

    Person(user, "Пользователь", "Инженер / руководитель / секретарь")

    Container_Boundary(app, "Ассистент ПБ") {
        Container(desktop, "Desktop-окно", "pywebview + Chromium", "Отображает UI в нативном окне")
        Container(frontend, "Frontend", "HTML/CSS/JS (vanilla)", "Три экрана: проверка / договор / письмо")
        Container(backend, "Backend", "FastAPI (MVC)", "Роутинг + очередь + пайплайны")
        Container(rag, "RAG-пакет", "Python (fire_safety_rag)", "Индексатор + ретривер по корпусу")
        ContainerDb(chroma, "ChromaDB", "Embedded векторная БД", "Индексированные чанки нормативки")
        ContainerDb(uploads, "Файловая система", "data/uploads, data/outputs", "Загруженные и сгенерированные файлы")
    }

    Container_Ext(ollama, "Ollama", "HTTP-сервис на 127.0.0.1:11434", "Запуск LLM (Qwen 2.5 7B/14B)")
    Container_Ext(embedder, "Sentence-transformers", "Python + PyTorch", "Модель intfloat/multilingual-e5-large")

    Rel(user, desktop, "Открывает окно")
    Rel(desktop, frontend, "Загружает страницы")
    Rel(frontend, backend, "REST", "/api/*")
    Rel(backend, rag, "Import", "retrieve()")
    Rel(backend, ollama, "REST", "/api/chat")
    Rel(rag, chroma, "Query", "top-k поиск")
    Rel(rag, embedder, "Embed", "запрос → вектор")
    Rel(backend, uploads, "Read/Write", "файлы")
```

## Ответственность контейнеров

| Контейнер | Технология | Ответственность |
|---|---|---|
| **Desktop-окно** | pywebview + Chromium/WebKit | Отображает web-UI как нативное окно; запускает backend в фоне |
| **Frontend** | HTML + vanilla JS + CSS | Три экрана: проверка документа, анализ договора, письмо. Отправляет запросы в `/api/*`, опрашивает `/api/tasks/{id}` |
| **Backend** | FastAPI (Python 3.11+) | HTTP-роуты (MVC: `views/`, `controllers/`, `services/`), очередь задач (1 воркер), пайплайны с промптами |
| **RAG-пакет** | ChromaDB + sentence-transformers | Индексация корпуса (batch), top-k поиск по запросу |
| **ChromaDB** | Embedded (файловый) | Векторное хранилище чанков нормативных актов |
| **Ollama** (внешний) | llama.cpp через HTTP | Загрузка LLM в память, инференс, стриминг ответов |

## Слои backend (MVC)

```
apps/backend/src/fire_safety_backend/
├── main.py                 # FastAPI app factory (собирает роутеры)
├── config.py               # env-based настройки
├── models/                 # M — Pydantic-схемы данных
├── views/                  # V — HTTP-роутеры (тонкие)
├── controllers/            # (промежуточный слой оркестрации, зарезервирован)
├── services/               # Бизнес-логика (upload → text)
├── pipelines/              # Пайплайны трёх функций (промпт + LLM + RAG)
├── infrastructure/         # Внешние зависимости
│   ├── llm.py              #   клиент Ollama
│   ├── queue.py            #   in-memory очередь задач
│   ├── parsers/            #   DOCX / PDF / OCR
│   └── generators/         #   DOCX-генератор писем
└── resources/              # Промпты и шаблоны, упакованные с приложением
    ├── prompts/
    └── templates/
```

## Границы деплоя

Всё крутится на одном сервере. Backend слушает `127.0.0.1:8000` (или
случайный свободный порт, если 8000 занят). Ollama — на `127.0.0.1:11434`.
Никаких сетевых обращений за пределы `127.0.0.1` не делается — офлайн-режим.
