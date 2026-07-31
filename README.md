# 🔥 Ассистент ПБ

**Локальный ИИ-ассистент для компании в сфере пожарной безопасности.**
Работает **полностью офлайн** на сервере компании. Четыре функции:

1. **Проверка документа** — орфография и пунктуация (LanguageTool + LLM).
2. **Юридический анализ договора** — риски с цветовой маркировкой, ссылки
   на ФЗ и ГК РФ, предложения правок.
3. **Официальное письмо** — превращает набросок в письмо на фирменном бланке
   ПожСервис (ГОСТ Р 7.0.97-2016); стиль подтягивается из архива реальных
   писем компании; поля редактируются прямо в интерфейсе перед скачиванием.
4. **Пакетная проверка договоров** — несколько файлов разом: тип каждого
   определяется автоматически, договоры уходят в юр. анализ, в конце —
   сводный DOCX-отчёт.

Плюс история задач, кнопки фидбека 👍/👎 и живой счётчик токенов во время
генерации.

---

## Оглавление

- [Что сделано в проекте — вводный файл](HANDOFF.md)
- [Пользователи: как добавить сотрудника](#пользователи-как-добавить-сотрудника)
- [Установка на сервер](docs/07-ops/install-server.md)
- [Быстрый старт (Windows, тестировщик)](docs/07-ops/install-windows.md)
- [Разработчику (macOS/Linux)](CONTRIBUTING.md)
- [Полная документация](docs/README.md) — 7 разделов: vision · product · architecture · design · quality · team · ops

## Пользователи: как добавить сотрудника

Без учётной записи в приложение не войти — **пароля по умолчанию нет**, и сами
записи не создаются. Заводит их администратор одной командой:

```bash
python scripts/add_user.py ivanov
```

Запускать можно **любым** python — скрипт сам найдёт venv проекта. Из папки
проекта, на сервере (или на машине, где стоит приложение).

Всё, что умеет скрипт:

```bash
python scripts/add_user.py ivanov              # завести сотрудника
python scripts/add_user.py ivanov --admin      # завести администратора
python scripts/add_user.py --list              # кто заведён
python scripts/add_user.py ivanov --disable    # закрыть доступ уволившемуся
python scripts/add_user.py ivanov --enable     # вернуть доступ
```

**Как сотрудник входит.** Открывает адрес сервера, вводит свой логин, жмёт
«Вход» — один раз. Дальше компьютер подставляет логин сам, и остаётся только
нажать кнопку. Пароля нет вовсе.

Если за одним компьютером работают несколько человек, на форме входа есть
ссылка **«не я»** — она стирает запомненный логин.

Что важно знать:

- **Логин должен существовать заранее.** Придумать его на ходу нельзя: иначе
  опечатка завела бы нового «сотрудника», и человек потерял бы доступ к своим
  документам.
- **Отключение — единственный способ отозвать доступ**, раз пароля нет. Оно же
  сразу завершает открытые сессии уволившегося.
- **Вход по логину не защищает от постороннего.** Логины предсказуемы
  (фамилии), поэтому сервер обязан стоять в доверенной сети — подробнее в
  [security.md](docs/05-quality/security.md).

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

# Индексация корпуса законов — .txt/.docx/.pdf (сканы через OCR) из packages/rag/corpus/
# (первый раз тянет эмбед-модель ~1.3 ГБ)
PYTHONPATH=apps/backend/src:packages/rag/src venv/bin/python scripts/index_corpus.py

# Опционально: архив реальных писем компании как образцы стиля для генерации
# (архив в git не попадает — коммерческие данные; путь укажите свой)
PYTHONPATH=apps/backend/src:packages/rag/src venv/bin/python scripts/index_letters.py --zip ~/письма.zip

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
#или
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
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
