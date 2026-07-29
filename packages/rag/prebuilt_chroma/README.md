# prebuilt_chroma

Готовая ChromaDB-коллекция `legal_corpus`, посчитанная из документов в
[`packages/rag/corpus/`](../corpus/) — эмбеддинги уже вычислены, поэтому
свежая установка не тратит несколько минут на их пересчёт на CPU без GPU
(`fire_safety_rag.seed.ensure_seeded()`, вызывается из `scripts/index_corpus.py`
перед обычной индексацией).

Заселяет **только** `legal_corpus` и только если у пользователя эта
коллекция ещё пустая — не трогает `letters_history` и любые другие
приватные коллекции, даже если они уже есть в `data/chroma/`.

## Как пересобрать

Нужно после любого изменения состава `packages/rag/corpus/` (добавили,
обновили или удалили документ):

```powershell
Remove-Item -Recurse -Force packages\rag\prebuilt_chroma\*.sqlite3, packages\rag\prebuilt_chroma\*  -Exclude README.md
$env:RAG_CHROMA_DIR = "packages\rag\prebuilt_chroma"
$env:PYTHONPATH = "apps\backend\src;packages\rag\src"
.\venv\Scripts\python.exe scripts\index_corpus.py --reset
git add packages/rag/prebuilt_chroma
```

Собирать нужно ровно той версией `chromadb`, что закреплена в
[`requirements-runtime.txt`](../../../requirements-runtime.txt) — on-disk
формат не гарантированно совместим между версиями библиотеки.
