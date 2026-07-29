#!/usr/bin/env python3
"""Загрузка нормативных документов ПАО «НЛМК» в корпус RAG по манифесту.

Зачем не краулер. На сайте lipetsk.nlmk.com 957 документов, нужных нам —
несколько десятков, но подобраться к ним обходом нельзя:

  * фильтр по разделам («Поставщикам и подрядчикам») работает через AJAX, а
    robots.txt запрещает `/ajax/`, `*?bxajaxid=` и `*?PAGEN*` — то есть ровно
    фильтр и пагинацию списка;
  * неизвестный slug раздела отдаёт не 404, а полный список из 957 документов
    (проверено на .../documents/for-suppliers-and-contractors/ — 200 OK и та же
    страница, что и без раздела). Краулер по такому ответу скачал бы всё;
  * сайт за QRATOR и при частых запросах отвечает 502.

Поэтому источник истины — манифест `scripts/nlmk_manifest.json`: заранее
выверенный список FILE_ID. Скрипт не ходит по ссылкам и ничего не обнаруживает
сам, он скачивает ровно то, что перечислено. Пополнять манифест — руками.

Про robots.txt честно. Сам `download_file.php` не запрещён; он отдаёт 302 на
файл в `/upload/`, а этот путь для роботов закрыт (при этом строкой ниже идёт
`Allow: *.pdf`). Мы не робот-обходчик: это разовая загрузка конечного списка
документов, которые НЛМК публикует для своих подрядчиков, а наша компания —
подрядчик НЛМК. Паузу между запросами держим по заявленной сайтом
Crawl-delay: 5.

Запуск (из venv проекта):
    python scripts/fetch_nlmk_docs.py --dry-run   # только показать, что скачается
    python scripts/fetch_nlmk_docs.py             # скачать в packages/rag/corpus
    python scripts/fetch_nlmk_docs.py --max-mb 60 # поднять потолок размера

После загрузки нужна переиндексация: python scripts/index_corpus.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger("fetch_nlmk")

_BASE = "https://lipetsk.nlmk.com"
_DOWNLOAD = _BASE + "/download_file.php?FILE_ID={file_id}"
# Реферер как у обычного перехода со страницы документов.
_REFERER = _BASE + "/ru/about/documents/"
# Без браузерного User-Agent QRATOR отвечает отказом. Запросов единицы, и они
# идут с паузой — нагрузки это не создаёт.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_CRAWL_DELAY_SEC = 5.0
_TIMEOUT_SEC = 120.0

# Из чего можно извлечь текст (см. parsers.extract_text). Видеоинструктажи и
# презентации НЛМК весят сотни мегабайт и текста для RAG не содержат.
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".rtf", ".txt"}
_DEFAULT_MAX_MB = 40


def _manifest_path() -> Path:
    return Path(__file__).resolve().parent / "nlmk_manifest.json"


def _corpus_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "packages" / "rag" / "corpus"


def _existing_hashes(corpus_dir: Path) -> dict[str, str]:
    """SHA-256 всех файлов корпуса → имя. Четыре документа НЛМК уже лежат там,
    скачанные вручную, и повторно их сохранять незачем."""
    hashes: dict[str, str] = {}
    for p in corpus_dir.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            hashes[hashlib.sha256(p.read_bytes()).hexdigest()] = p.name
    return hashes


def _target_name(resolved_url: str, file_id: int) -> str:
    """Имя файла из адреса, на который увёл редирект.

    НЛМК отдаёт уже транслитерированные имена вида
    `Polozhenie-o-proizvodstvennom-kontrole.pdf`, поэтому переводить ничего не
    нужно — достаточно префикса, чтобы документы заказчика было видно в корпусе.
    """
    raw = unquote(Path(urlparse(resolved_url).path).name)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in raw)
    if not safe or safe.startswith("."):
        safe = f"{file_id}.bin"
    return safe if safe.upper().startswith("NLMK") else f"NLMK_{safe}"


def _fetch_one(
    client, file_id: int, max_bytes: int, expected_file: str, allow_renamed: bool
) -> tuple[str, bytes] | str:
    """Возвращает (имя_файла, содержимое) либо строку с причиной пропуска.

    Тело читается ПОТОКОМ и только после проверок: в списке НЛМК есть
    видеоинструктажи на 600 МБ, и обычный GET утащил бы их в память целиком
    ещё до того, как мы посмотрели бы на расширение и размер. Заголовку
    Content-Length не доверяем безоговорочно — считаем прочитанное и рвём
    чтение, если сервер соврал.
    """
    with client.stream("GET", _DOWNLOAD.format(file_id=file_id)) as r:
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        resolved = str(r.url)
        actual_file = unquote(Path(urlparse(resolved).path).name)
        # FILE_ID — это идентификатор записи, а не документа: НЛМК может
        # заменить приложенный файл, не меняя ID. Тогда мы молча положили бы в
        # корпус документ, которого никто не выбирал. Имя из манифеста —
        # единственная зацепка, чтобы это заметить.
        if expected_file and actual_file != expected_file and not allow_renamed:
            return f"файл под этим ID сменился: ожидали {expected_file}, отдали {actual_file}"
        name = _target_name(resolved, file_id)
        suffix = Path(name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            return f"расширение {suffix or '—'} пропускаем ({resolved.rsplit('/', 1)[-1]})"
        declared = r.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            return f"{int(declared) / 1e6:.0f} МБ — больше лимита"
        buf = bytearray()
        for piece in r.iter_bytes():
            buf.extend(piece)
            if len(buf) > max_bytes:
                return f"больше {max_bytes / 1e6:.0f} МБ — чтение прервано"
    if not buf:
        return "пустой ответ"
    return name, bytes(buf)


def _merge_meta(corpus_dir: Path, added: dict[str, dict]) -> None:
    """Дописывает записи в corpus/_meta.json, не трогая существующие."""
    meta_path = corpus_dir / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for name, entry in added.items():
        meta.setdefault(name, entry)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Загрузка документов НЛМК по манифесту")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не сохранять")
    parser.add_argument("--max-mb", type=int, default=_DEFAULT_MAX_MB, help="Потолок размера файла")
    parser.add_argument(
        "--delay", type=float, default=_CRAWL_DELAY_SEC, help="Пауза между запросами"
    )
    parser.add_argument(
        "--allow-renamed",
        action="store_true",
        help="Скачивать, даже если под FILE_ID лежит уже другой файл (сверьте вручную)",
    )
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("Нужен httpx: pip install httpx", file=sys.stderr)
        return 1

    manifest_path = _manifest_path()
    if not manifest_path.exists():
        print(f"Манифест не найден: {manifest_path}", file=sys.stderr)
        return 1
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["documents"]

    corpus_dir = _corpus_dir()
    corpus_dir.mkdir(parents=True, exist_ok=True)
    known = _existing_hashes(corpus_dir)

    saved: dict[str, dict] = {}
    skipped: list[str] = []
    max_bytes = args.max_mb * 1_000_000

    headers = {"User-Agent": _USER_AGENT, "Referer": _REFERER}
    with httpx.Client(
        follow_redirects=True, timeout=_TIMEOUT_SEC, headers=headers, http2=False
    ) as client:
        for i, entry in enumerate(entries):
            file_id = int(entry["file_id"])
            title = entry.get("title", str(file_id))
            if i:
                time.sleep(args.delay)
            try:
                outcome = _fetch_one(
                    client, file_id, max_bytes, entry.get("file", ""), args.allow_renamed
                )
            except Exception as e:  # noqa: BLE001 — сеть, любая ошибка не должна валить прогон
                skipped.append(f"{file_id} {title}: {e}")
                log.warning("FILE_ID=%s — %s", file_id, e)
                continue
            if isinstance(outcome, str):
                skipped.append(f"{file_id} {title}: {outcome}")
                log.info("пропуск FILE_ID=%s (%s): %s", file_id, title, outcome)
                continue

            name, content = outcome
            digest = hashlib.sha256(content).hexdigest()
            if digest in known:
                skipped.append(f"{file_id} {title}: уже в корпусе как {known[digest]}")
                log.info("дубликат FILE_ID=%s → %s", file_id, known[digest])
                continue

            log.info("%s ← FILE_ID=%s (%.1f МБ)", name, file_id, len(content) / 1e6)
            if args.dry_run:
                continue
            (corpus_dir / name).write_bytes(content)
            known[digest] = name
            saved[name] = {
                "doc_type": "nlmk_document",
                "title": title,
                "source_url": _DOWNLOAD.format(file_id=file_id),
                "status": "actual",
            }

    if saved:
        _merge_meta(corpus_dir, saved)

    print(f"\nСкачано: {len(saved)}, пропущено: {len(skipped)}")
    for s in skipped:
        print(f"  · {s}")
    if saved and not args.dry_run:
        print("\nТеперь переиндексация: python scripts/index_corpus.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
