#!/usr/bin/env python3
"""Проверка согласованности корпуса нормативки: файлы ↔ git ↔ _meta.json ↔ индекс.

Зачем: .gitignore перечисляет разрешённые к публикации документы ПОФАЙЛОВО, и
это уже подвело — Гражданский кодекс, КоАП и ППР-1479 лежали в корпусе локально,
но в git не попали. На свежей установке (git clone / автообновление) юридический
анализ договоров работал вообще без ГК РФ, своего главного источника, и никак об
этом не сообщал. Отдельно всплыло, что индекс собран на 8 файлах из 27 — корпус
пополнили, а переиндексацию не запустили.

Оба отказа тихие: приложение работает, просто отвечает хуже. Эта проверка делает
их громкими.

Запуск:
    python scripts/check_corpus.py            # только файлы/git/метаданные
    python scripts/check_corpus.py --index    # ещё и сверка с ChromaDB
Ненулевой код возврата — если что-то расходится (годится для CI).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и первый же импорт упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()


ROOT = Path(__file__).resolve().parent.parent
for _rel in ("apps/backend/src", "packages/rag/src"):
    sys.path.insert(0, str(ROOT / _rel))

# Проверка обязана идти в тех же условиях, что боевой запуск, иначе она
# проверяет не то приложение, которое поедет к заказчику. netguard не только
# запрещает сеть, но и выставляет HF_HUB_OFFLINE=1 — обязательно ДО импорта
# huggingface_hub, иначе флаг не читается.
#
# Практическое следствие: без этого скрипт печатал предупреждение HF Hub про
# «unauthenticated requests» ровно там, где чек-лист приёмки требует убедиться,
# что обращений к huggingface.co нет. Пункт, который сам себе противоречит,
# перестают проверять.
from fire_safety_backend.infrastructure import netguard  # noqa: E402

netguard.install()
CORPUS = ROOT / "packages" / "rag" / "corpus"
META = CORPUS / "_meta.json"
SIDECAR_NAMES = {"_meta.json", ".gitkeep", "README.md"}
KNOWN_STATUSES = {"actual", "superseded", "draft"}


def _corpus_files(corpus_dir: Path | None = None) -> list[Path]:
    """Документы ОДНОГО домена. Обход не рекурсивный: подпапка — это другой
    домен со своей коллекцией и своими метаданными, и складывать их в один
    список значило бы проверять несопоставимые вещи."""
    base = corpus_dir or CORPUS
    return sorted(
        p
        for p in base.iterdir()
        if p.is_file() and p.name not in SIDECAR_NAMES and not p.name.startswith(".")
    )


def _git_tracked(corpus_dir: Path | None = None) -> set[str]:
    base = corpus_dir or CORPUS
    r = subprocess.run(
        ["git", "ls-files", str(base.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {Path(line).name for line in r.stdout.splitlines() if line.strip()}


def _indexed_sources(collection: str | None = None) -> set[str] | None:
    """Имена файлов, реально попавшие в ChromaDB. None — индекс недоступен."""
    try:
        import chromadb
        from fire_safety_rag import config

        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        col = client.get_collection(collection or config.COLLECTION_NAME)
        got = col.get(include=["metadatas"])
        return {m.get("source") for m in got.get("metadatas", []) if m}
    except Exception as e:  # noqa: BLE001
        print(f"  (индекс недоступен: {e})")
        return None


def _filtered_search_works() -> str:
    """Пустая строка — поиск работает. Иначе текст ошибки.

    Проверяется именно поиск С ФИЛЬТРОМ отменённых редакций — тот самый, каким
    пользуется ретривер. Сверки «файл есть в индексе» для этого мало:
    повреждённая коллекция честно отдавала все 3281 документ через get(), но
    падала на ЛЮБОМ векторном запросе с where — то есть при каждом реальном
    обращении из анализа договора. Такая порча иначе обнаруживается только
    сбоем у пользователя.
    """
    try:
        import fire_safety_rag

        if not fire_safety_rag.is_ready():
            return "индекс пуст или не создан"
        fire_safety_rag.retrieve("требования пожарной безопасности", top_k=1)
        return fire_safety_rag.search_failure()
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка корпуса")
    parser.add_argument("--index", action="store_true", help="Сверить ещё и с ChromaDB")
    parser.add_argument(
        "--domain",
        default="pb",
        choices=["pb", "nlmk"],
        help="Какой домен проверять: pb — нормативка РФ, nlmk — документы заказчика",
    )
    args = parser.parse_args()

    from fire_safety_rag import config as rag_config

    corpus_dir = rag_config.corpus_dir_for_domain(args.domain)
    collection = rag_config.collection_for_domain(args.domain)
    if not corpus_dir.exists():
        print(f"Папка домена «{args.domain}» не найдена: {corpus_dir}")
        return 0

    meta_path = corpus_dir / "_meta.json"
    files = _corpus_files(corpus_dir)
    names = {p.name for p in files}
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    tracked = _git_tracked(corpus_dir)
    problems = 0

    print(f"Домен «{args.domain}» → коллекция «{collection}»")
    print(f"Файлов: {len(files)} · записей в _meta.json: {len(meta)}")

    # Отменённые редакции публиковать не обязательно — они всё равно
    # исключаются из выдачи. Требуем git только от действующих документов.
    actual_names = {n for n in names if meta.get(n, {}).get("status") != "superseded"}
    untracked = sorted(actual_names - tracked)
    if untracked:
        problems += len(untracked)
        print(f"\n[X] Действующие, но НЕ отслеживаются git ({len(untracked)}) —")
        print("   на свежей установке этих документов у пользователя не будет:")
        for n in untracked:
            print(f"     {n}")
        print("   Добавьте «!packages/rag/corpus/<имя>» в .gitignore и `git add`")

    no_meta = sorted(names - set(meta))
    if no_meta:
        problems += len(no_meta)
        print(f"\n[!]Без записи в _meta.json ({len(no_meta)}) — не будет ни типа, ни статуса:")
        for n in no_meta:
            print(f"     {n}")

    orphan_meta = sorted(set(meta) - names)
    if orphan_meta:
        problems += len(orphan_meta)
        print(f"\n[!]Запись в _meta.json есть, файла нет ({len(orphan_meta)}):")
        for n in orphan_meta:
            print(f"     {n}")

    bad_status = sorted(
        n for n, v in meta.items() if v.get("status") and v["status"] not in KNOWN_STATUSES
    )
    if bad_status:
        problems += len(bad_status)
        print(f"\n[X] Неизвестный status ({len(bad_status)}), допустимы {sorted(KNOWN_STATUSES)}:")
        for n in bad_status:
            print(f"     {n}: {meta[n]['status']}")

    if args.index:
        indexed = _indexed_sources(collection)
        if indexed is not None:
            actual = {n for n in names if meta.get(n, {}).get("status") != "superseded"}
            missing = sorted(actual - indexed)
            if missing:
                problems += len(missing)
                print(
                    f"\n[X] В корпусе есть, в индексе НЕТ ({len(missing)}) — нужна переиндексация:"
                )
                for n in missing[:15]:
                    print(f"     {n}")
                print("   python scripts/index_corpus.py --reset")

            failure = _filtered_search_works()
            if failure:
                problems += 1
                print(f"\n[X] Поиск с фильтром отменённых редакций НЕ работает: {failure}")
                print("   Индекс повреждён — переиндексируйте:")
                print("   python scripts/index_corpus.py --reset")
            else:
                print("\n[OK] Поиск с фильтром отменённых редакций работает")

    if problems:
        print(f"\nПроблем: {problems}")
        return 1
    print("\n[OK] Корпус согласован")
    return 0


if __name__ == "__main__":
    sys.exit(main())
