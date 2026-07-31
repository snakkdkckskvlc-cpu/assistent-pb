"""Контроль целостности кода.

Самый важный тест здесь — `test_line_endings_do_not_count_as_change`. В
репозитории `core.autocrlf=true` и нет `.gitattributes`: в индексе файлы с LF,
в рабочем дереве Windows получает CRLF. Если бы это считалось правкой,
приложение блокировало бы запуск на ровном месте, то есть рабочий инструмент у
сотрудника превращался бы в кирпич. Ложное срабатывание здесь дороже
пропущенной правки.

Тесты работают на синтетическом дереве, а не на настоящем репозитории. Тест
«репозиторий совпадает со своим манифестом» намеренно НЕ добавлен: он краснел
бы при каждой правке кода до пересбора манифеста, то есть постоянно во время
разработки. Эту роль играет шаг `--check` в CI, который сверяет уже
закоммиченное дерево.
"""

from __future__ import annotations

import json
from pathlib import Path

from fire_safety_backend.infrastructure import integrity


def _tree(root: Path) -> Path:
    """Минимальное дерево из тех же каталогов, которые покрывает манифест."""
    files = {
        "apps/backend/src/fire_safety_backend/config.py": "SETTING = 1\n",
        "apps/backend/src/fire_safety_backend/resources/prompts/legal.txt": "Промпт\n",
        "apps/desktop/src/fire_safety_desktop/main.py": "def main(): pass\n",
        "packages/rag/src/fire_safety_rag/retriever.py": "TOP_K = 5\n",
        "apps/desktop/frontend/app.js": "const a = 1;\n",
        "scripts/index_corpus.py": "print('ok')\n",
        "bootstrap.ps1": "Write-Host 'install'\n",
        "requirements-runtime.txt": "fastapi\n",
        "apps/backend/src/fire_safety_backend/resources/templates/letterhead.docx": "бланк\n",
        # Не должно попасть в манифест:
        "apps/backend/tests/unit/test_x.py": "def test_x(): pass\n",
        "apps/backend/src/fire_safety_backend/__pycache__/config.cpython-312.pyc": "мусор\n",
        "packages/rag/corpus/69-fz.txt": "текст закона\n",
        "data/uploads/договор.docx": "документ\n",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _build_and_save(root: Path) -> None:
    integrity.manifest_path(root).write_text(
        integrity.serialize(integrity.build(root)), encoding="utf-8", newline="\n"
    )


# --- Что покрывается ---


def test_covers_runtime_code(tmp_path: Path) -> None:
    covered = {p.as_posix() for p in integrity.iter_covered(_tree(tmp_path))}
    assert "apps/backend/src/fire_safety_backend/config.py" in covered
    assert "apps/desktop/frontend/app.js" in covered
    assert "scripts/index_corpus.py" in covered
    assert "bootstrap.ps1" in covered
    assert "requirements-runtime.txt" in covered


def test_covers_prompts(tmp_path: Path) -> None:
    """Подменённый промпт меняет поведение модели не меньше правки кода."""
    covered = {p.as_posix() for p in integrity.iter_covered(_tree(tmp_path))}
    assert "apps/backend/src/fire_safety_backend/resources/prompts/legal.txt" in covered


def test_covers_letterhead(tmp_path: Path) -> None:
    """В бланке банковские реквизиты, и он лежит в git — значит поставляется
    вместе с кодом. Подмена счёта в исходящем письме — классическая схема
    мошенничества, поэтому правка бланка обязана быть видна."""
    covered = {p.as_posix() for p in integrity.iter_covered(_tree(tmp_path))}
    assert "apps/backend/src/fire_safety_backend/resources/templates/letterhead.docx" in covered


def test_excludes_what_would_cause_false_alarms(tmp_path: Path) -> None:
    covered = {p.as_posix() for p in integrity.iter_covered(_tree(tmp_path))}
    # Каждое исключение по делу: pyc создаётся при запуске, тесты на работу не
    # влияют, корпус и data — данные, а не код.
    assert not any("__pycache__" in c for c in covered)
    assert not any("/tests/" in c for c in covered)
    assert not any(c.startswith("packages/rag/corpus") for c in covered)
    assert not any(c.startswith("data/") for c in covered)


# --- Сверка ---


def test_untouched_tree_is_ok(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _build_and_save(root)
    assert integrity.verify(root).ok is True


def test_line_endings_do_not_count_as_change(tmp_path: Path) -> None:
    """Ключевой тест: иначе приложение — кирпич на любой машине с другой
    настройкой core.autocrlf, и на CI (ubuntu, LF) тоже."""
    root = _tree(tmp_path)
    target = root / "apps/backend/src/fire_safety_backend/config.py"
    target.write_bytes(b"SETTING = 1\nOTHER = 2\n")
    _build_and_save(root)

    target.write_bytes(b"SETTING = 1\r\nOTHER = 2\r\n")
    assert integrity.verify(root).ok is True, "CRLF посчитан правкой"

    target.write_bytes(b"SETTING = 1\nOTHER = 2\n")
    assert integrity.verify(root).ok is True, "LF посчитан правкой"


def test_edited_file_is_detected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "apps/backend/src/fire_safety_backend/config.py").write_text(
        "SETTING = 999\n", encoding="utf-8"
    )
    report = integrity.verify(root)
    assert report.ok is False
    assert report.changed == ["apps/backend/src/fire_safety_backend/config.py"]
    assert integrity.status(root) == "changed"


def test_deleted_file_is_detected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "apps/desktop/frontend/app.js").unlink()
    report = integrity.verify(root)
    assert report.ok is False
    assert report.missing == ["apps/desktop/frontend/app.js"]


def test_added_file_is_detected(tmp_path: Path) -> None:
    """Новый модуль — тоже правка кода, молчать о нём нельзя."""
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "apps/backend/src/fire_safety_backend/подсадной.py").write_text(
        "import os\n", encoding="utf-8"
    )
    report = integrity.verify(root)
    assert report.ok is False
    assert report.extra == ["apps/backend/src/fire_safety_backend/подсадной.py"]


def test_substituted_letterhead_is_detected(tmp_path: Path) -> None:
    """Главный смысл включения бланка в манифест: подменённые банковские
    реквизиты в письме заказчику не должны уйти молча."""
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "apps/backend/src/fire_safety_backend/resources/templates/letterhead.docx").write_text(
        "бланк с чужим расчётным счётом\n", encoding="utf-8"
    )
    report = integrity.verify(root)
    assert report.ok is False
    assert report.changed == [
        "apps/backend/src/fire_safety_backend/resources/templates/letterhead.docx"
    ]


def test_editing_excluded_file_is_not_a_change(tmp_path: Path) -> None:
    """Рабочие документы пользователя — не код: их появление и правка не имеют
    отношения к целостности программы."""
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "data/uploads/новый.docx").write_text("документ\n", encoding="utf-8")
    (root / "packages/rag/corpus/новый-закон.txt").write_text("текст\n", encoding="utf-8")
    assert integrity.verify(root).ok is True


def test_problems_list_is_human_readable(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _build_and_save(root)
    (root / "apps/backend/src/fire_safety_backend/config.py").write_text("x\n", encoding="utf-8")
    problems = integrity.verify(root).problems
    assert problems == ["изменён: apps/backend/src/fire_safety_backend/config.py"]


# --- Отказы самой проверки ---


def test_missing_manifest_is_a_clear_reason_not_an_exception(tmp_path: Path) -> None:
    """Запуск в таком состоянии придётся блокировать так же, как при правке, —
    значит нужна причина, а не падение."""
    report = integrity.verify(_tree(tmp_path))
    assert report.ok is False
    assert integrity.MANIFEST_NAME in report.reason
    assert integrity.status(tmp_path) == "unknown"


def test_corrupted_manifest_is_a_clear_reason(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    integrity.manifest_path(root).write_text("{это не json", encoding="utf-8")
    report = integrity.verify(root)
    assert report.ok is False
    assert "повреждён" in report.reason


def test_manifest_without_files_key_is_a_clear_reason(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    integrity.manifest_path(root).write_text('{"algo": "sha256"}', encoding="utf-8")
    assert integrity.verify(root).ok is False


# --- Формат манифеста ---


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    """Одинаковое дерево обязано давать побайтово одинаковый манифест: иначе
    `--check` в CI бессмыслен, а каждый пересбор шумит в диффе."""
    root = _tree(tmp_path)
    assert integrity.serialize(integrity.build(root)) == integrity.serialize(integrity.build(root))


def test_manifest_uses_posix_paths(tmp_path: Path) -> None:
    """Манифест собирается на Windows, а проверяется и на CI (ubuntu)."""
    manifest = integrity.build(_tree(tmp_path))
    assert all("\\" not in key for key in manifest["files"])


def test_manifest_has_no_timestamp(tmp_path: Path) -> None:
    manifest = json.loads(integrity.serialize(integrity.build(_tree(tmp_path))))
    assert set(manifest) == {"algo", "normalize", "files"}
