"""Контроль целостности кода: не правил ли приложение кто-то посторонний.

Манифест `integrity.json` в корне проекта хранит SHA-256 каждого файла
рантайма и коммитится в git. При запуске дерево сверяется с манифестом, и при
расхождении десктопная обёртка отказывается стартовать (см.
fire_safety_desktop/main.py).

### Честная граница

Это НЕ защита от злоумышленника. Кто может отредактировать код, может убрать и
вызов этой проверки, и пересчитать манифест — сам манифест защищён только тем,
что лежит в git. Проверка ловит другое, и это реальные сценарии:

- сотрудник или ИТ-специалист «поправил» файл;
- автообновление сорвалось на середине и оставило смешанное дерево;
- файл побился при копировании папки или на диске.

### Почему нормализуются переводы строк

В репозитории `core.autocrlf=true` и нет `.gitattributes`: в индексе файлы
хранятся с LF, а в рабочем дереве Windows получает CRLF (проверено
`git ls-files --eol` → `i/lf w/crlf`). Без нормализации манифест, собранный на
Windows, не совпал бы ни с CI на ubuntu, ни с машиной, где git настроен иначе,
и приложение блокировало бы запуск на ровном месте. Ложное срабатывание здесь
дороже пропущенной правки: оно превращает рабочий инструмент в кирпич.

Манифест намеренно НЕ содержит даты сборки: одинаковое дерево обязано давать
побайтово одинаковый манифест, иначе `--check` в CI бессмыслен, а каждый
пересбор шумит в диффе.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

MANIFEST_NAME = "integrity.json"
ALGO = "sha256"

# Каталоги рантайма и расширения, которые в них считаются кодом.
_COVERED_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # .txt здесь — это resources/prompts: подменённый промпт меняет поведение
    # модели не меньше, чем правка кода, и попасть в проверку обязан.
    # letterhead.docx под него не подпадает: он лежит в templates/, а тот
    # исключён (файл в git не попадает и живёт только на машине сотрудника).
    ("apps/backend/src", (".py", ".txt")),
    ("apps/desktop/src", (".py",)),
    ("packages/rag/src", (".py",)),
    ("apps/desktop/frontend", (".js", ".html", ".css", ".svg")),
    ("scripts", (".py",)),
)

# Отдельные файлы вне этих каталогов: установщик и список зависимостей влияют
# на то, что окажется на машине, не меньше самого кода. Скрипты LanguageTool
# перечислены поимённо, а не каталогом: рядом с ними распаковывается JDK на
# 430 МБ, и обход этого дерева на каждом старте свёл бы 27 мс проверки на нет.
_COVERED_FILES: tuple[str, ...] = (
    "bootstrap.ps1",
    "requirements-runtime.txt",
    "tools/languagetool/setup.ps1",
    "tools/languagetool/start.ps1",
    "tools/languagetool/setup.sh",
    "tools/languagetool/start.sh",
)

# Каталоги, которых в манифесте быть не должно. Каждое исключение по делу —
# лишнее превратит инструмент в кирпич на ложном срабатывании:
#   __pycache__/.pytest_cache — создаются при запуске;
#   tests — на работу приложения не влияют;
#   corpus/prebuilt_chroma/data — данные, а не код;
#   resources/templates — там letterhead.docx, он в git не попадает и живёт
#   ТОЛЬКО на машине сотрудника (коммерческие данные).
_EXCLUDED_PARTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "tests",
        "corpus",
        "prebuilt_chroma",
        "data",
        "templates",
    }
)


@dataclass(frozen=True)
class Report:
    """Результат сверки. Пустой во всех списках — дерево совпало."""

    ok: bool
    reason: str
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        """Все расхождения одним списком — для сообщения пользователю."""
        return (
            [f"изменён: {p}" for p in self.changed]
            + [f"пропал: {p}" for p in self.missing]
            + [f"лишний: {p}" for p in self.extra]
        )


def project_root() -> Path:
    return config.PROJECT_DIR


def manifest_path(root: Path | None = None) -> Path:
    return (root or project_root()) / MANIFEST_NAME


def _is_excluded(rel: Path) -> bool:
    return any(part in _EXCLUDED_PARTS for part in rel.parts)


def digest(path: Path) -> str:
    """SHA-256 содержимого с нормализованными переводами строк."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def iter_covered(root: Path | None = None) -> list[Path]:
    """Файлы рантайма, относительными путями, в стабильном порядке."""
    base = root or project_root()
    found: list[Path] = []
    for rel_dir, suffixes in _COVERED_DIRS:
        directory = base / rel_dir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            rel = path.relative_to(base)
            if _is_excluded(rel):
                continue
            found.append(rel)
    for name in _COVERED_FILES:
        if (base / name).is_file():
            found.append(Path(name))
    # as_posix в ключе: манифест обязан читаться одинаково на Windows и Linux.
    return sorted(found, key=lambda p: p.as_posix())


def build(root: Path | None = None) -> dict:
    base = root or project_root()
    return {
        "algo": ALGO,
        "normalize": "crlf->lf",
        "files": {rel.as_posix(): digest(base / rel) for rel in iter_covered(base)},
    }


def serialize(manifest: dict) -> str:
    """Стабильное представление: одинаковое дерево → одинаковые байты."""
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def verify(root: Path | None = None) -> Report:
    """Сверяет дерево с манифестом. Исключений не бросает — отдаёт Report.

    Отсутствие или порча манифеста — НЕ «всё хорошо» и не падение: это
    отдельная причина с внятным текстом, потому что запуск в таком состоянии
    придётся блокировать так же, как при изменённом файле.
    """
    base = root or project_root()
    path = manifest_path(base)
    if not path.is_file():
        return Report(ok=False, reason=f"Файл {MANIFEST_NAME} не найден — целостность не проверить")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        expected: dict[str, str] = manifest["files"]
        if not isinstance(expected, dict):
            raise TypeError("files должен быть объектом")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return Report(ok=False, reason=f"Файл {MANIFEST_NAME} повреждён: {e}")

    actual = {rel.as_posix(): digest(base / rel) for rel in iter_covered(base)}

    changed = sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k])
    missing = sorted(expected.keys() - actual.keys())
    # Новый модуль в дереве — тоже правка кода, и молчать о нём нельзя.
    extra = sorted(actual.keys() - expected.keys())

    if not (changed or missing or extra):
        return Report(ok=True, reason=f"Совпало файлов: {len(actual)}")

    parts = []
    if changed:
        parts.append(f"изменено {len(changed)}")
    if missing:
        parts.append(f"пропало {len(missing)}")
    if extra:
        parts.append(f"добавлено {len(extra)}")
    return Report(
        ok=False,
        reason="Код изменён: " + ", ".join(parts),
        changed=changed,
        missing=missing,
        extra=extra,
    )


def status(root: Path | None = None) -> str:
    """Одно из "ok" / "changed" / "unknown" — для /api/health."""
    report = verify(root)
    if report.ok:
        return "ok"
    if report.changed or report.missing or report.extra:
        return "changed"
    return "unknown"
