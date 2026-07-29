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

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "packages" / "rag" / "corpus"
META = CORPUS / "_meta.json"
SIDECAR_NAMES = {"_meta.json", ".gitkeep"}
KNOWN_STATUSES = {"actual", "superseded", "draft"}


def _corpus_files() -> list[Path]:
    return sorted(
        p
        for p in CORPUS.iterdir()
        if p.is_file() and p.name not in SIDECAR_NAMES and not p.name.startswith(".")
    )


def _git_tracked() -> set[str]:
    r = subprocess.run(
        ["git", "ls-files", str(CORPUS.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {Path(line).name for line in r.stdout.splitlines() if line.strip()}


def _indexed_sources() -> set[str] | None:
    """Имена файлов, реально попавшие в ChromaDB. None — индекс недоступен."""
    try:
        import chromadb
        from fire_safety_rag import config

        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        col = client.get_collection(config.COLLECTION_NAME)
        got = col.get(include=["metadatas"])
        return {m.get("source") for m in got.get("metadatas", []) if m}
    except Exception as e:  # noqa: BLE001
        print(f"  (индекс недоступен: {e})")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка корпуса нормативки")
    parser.add_argument("--index", action="store_true", help="Сверить ещё и с ChromaDB")
    args = parser.parse_args()

    files = _corpus_files()
    names = {p.name for p in files}
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    tracked = _git_tracked()
    problems = 0

    print(f"Файлов в корпусе: {len(files)} · записей в _meta.json: {len(meta)}")

    # Отменённые редакции публиковать не обязательно — они всё равно
    # исключаются из выдачи. Требуем git только от действующих документов.
    actual_names = {n for n in names if meta.get(n, {}).get("status") != "superseded"}
    untracked = sorted(actual_names - tracked)
    if untracked:
        problems += len(untracked)
        print(f"\n❌ Действующие, но НЕ отслеживаются git ({len(untracked)}) —")
        print("   на свежей установке этих документов у пользователя не будет:")
        for n in untracked:
            print(f"     {n}")
        print("   Добавьте «!packages/rag/corpus/<имя>» в .gitignore и `git add`")

    no_meta = sorted(names - set(meta))
    if no_meta:
        problems += len(no_meta)
        print(f"\n⚠  Без записи в _meta.json ({len(no_meta)}) — не будет ни типа, ни статуса:")
        for n in no_meta:
            print(f"     {n}")

    orphan_meta = sorted(set(meta) - names)
    if orphan_meta:
        problems += len(orphan_meta)
        print(f"\n⚠  Запись в _meta.json есть, файла нет ({len(orphan_meta)}):")
        for n in orphan_meta:
            print(f"     {n}")

    bad_status = sorted(
        n for n, v in meta.items() if v.get("status") and v["status"] not in KNOWN_STATUSES
    )
    if bad_status:
        problems += len(bad_status)
        print(f"\n❌ Неизвестный status ({len(bad_status)}), допустимы {sorted(KNOWN_STATUSES)}:")
        for n in bad_status:
            print(f"     {n}: {meta[n]['status']}")

    if args.index:
        indexed = _indexed_sources()
        if indexed is not None:
            actual = {n for n in names if meta.get(n, {}).get("status") != "superseded"}
            missing = sorted(actual - indexed)
            if missing:
                problems += len(missing)
                print(
                    f"\n❌ В корпусе есть, в индексе НЕТ ({len(missing)}) — нужна переиндексация:"
                )
                for n in missing[:15]:
                    print(f"     {n}")
                print("   python scripts/index_corpus.py --reset")

    if problems:
        print(f"\nПроблем: {problems}")
        return 1
    print("\n✅ Корпус согласован")
    return 0


if __name__ == "__main__":
    sys.exit(main())
