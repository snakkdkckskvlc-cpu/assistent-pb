# Диагностика проблем

## В шапке окна красная надпись «Ollama недоступна»

1. Проверьте, что Ollama установлена: `ollama --version`.
2. Проверьте, что она запущена: `curl http://127.0.0.1:11434/api/tags`
   должен вернуть JSON.
3. Windows: значок ламы в системном трее у часов. Если нет — Пуск →
   набрать «Ollama» → запустить.
4. macOS: `brew services start ollama` или `ollama serve &`.

## «Модель qwen2.5:… не установлена»

```bash
ollama pull qwen2.5:7b-instruct   # для теста
ollama pull qwen2.5:14b-instruct-q4_K_M   # для боевого
```

## В шапке «нормативная база не подключена»

Не проиндексирована. Запустите:

```bash
PYTHONPATH=packages/rag/src python -m fire_safety_rag.indexer
```

Первый запуск скачает эмбед-модель (~1.3 ГБ, нужен интернет). Последующие —
только новые файлы (идемпотентно по SHA-256).

## Задача висит больше 15 минут

- На CPU 7B-модель обрабатывает договор 3–8 минут, 14B — до 15 минут. Это норма.
- Если больше — проверьте оперативную память: `Activity Monitor` (macOS) /
  `Диспетчер задач` (Windows). Если LLM выгрузился из памяти — увеличьте
  RAM или перейдите на меньшую модель.

## Тесты падают с `RuntimeError: Queue is bound to a different event loop`

Убедитесь, что установлена версия `>= 0.2.0` — в ней очередь создаётся
в `start()`, а не при импорте. Обновите пакет: `pip install -e apps/backend --force-reinstall`.

## Клик по «📎 Выбрать файл» ничего не открывает

- В версии 0.2.0+ исправлено: label связан с input через `for`.
- Проверьте, что у вас `apps/desktop/frontend/views/spellcheck.html`
  содержит `<label ... for="file">` и `<input id="file" class="visually-hidden">`.

## PyInstaller / .app не запускается на macOS

- Проверьте `~/Library/Logs/AssistentPB.log` — там лог launcher-скрипта.
- Обычно проблема: путь к venv в `Contents/MacOS/launcher` захардкожен.
  Пересоберите: `scripts/build_macos_app.sh`.

## Ошибка индексации ChromaDB `SQL error`

- Обычно повреждён файл `data/chroma/chroma.sqlite3`. Удалите каталог
  `data/chroma/` и перезапустите индексацию с флагом `--reset`.

## Windows: `START.bat` мигает и закрывается

- Причина: антивирус блокирует `.bat`/`.ps1`.
- Правой кнопкой на `START.bat` → Свойства → снять галочку «Заблокирован».
- Или временно добавить папку проекта в исключения антивируса.
- Или запустить руками:
  `powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1`.

## Куда прислать логи разработчику

- **Windows**: `bootstrap.log` в корне проекта.
- **macOS**: `~/Library/Logs/AssistentPB.log`.
- **Linux**: вывод команды запуска uvicorn.
- **Backend**: если приложение запущено, логи идут в консоль uvicorn.

Дополнительно приложите:
- Версию ОС (`winver` на Windows, `sw_vers` на macOS).
- Вывод `ollama list`.
- Скриншот шапки окна (там видно модель и статус RAG).
