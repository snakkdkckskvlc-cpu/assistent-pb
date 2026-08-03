"""Предохранитель от манифеста, с которым приложение не запустится ни у кого.

Манифест целостности собирается по РАБОЧЕМУ ДЕРЕВУ, а сверяется на машине, где
дерево получено из git. Файл, который лежит на диске у автора манифеста, но
игнорируется git, попадает в манифест и не попадает к пользователю — и
приложение отказывается стартовать у всех, кроме автора.

Это уже происходило дважды: letterhead_raw.docx убрали из git (вторая копия
банковских реквизитов в истории), на диске он остался, следующий пересбор внёс
его обратно, и в origin/main приехал манифест, с которым приложение кирпич.

Тесты работают на настоящем временном git-репозитории: предмет проверки —
именно поведение git, подменять его фейком бессмысленно.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Минимальный репозиторий: один отслеживаемый файл, один игнорируемый."""
    if _git(tmp_path, "init", "--quiet").returncode != 0:
        pytest.skip("git недоступен")
    # Повторяет боевой .gitignore: каталог закрыт целиком, один файл возвращён
    # правилом-исключением. Именно на нём ломалась первая версия проверки.
    (tmp_path / ".gitignore").write_text("templates/**\n!templates/keep.docx\n", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "code.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "templates" / "keep.docx").write_text("бланк\n", encoding="utf-8")
    (tmp_path / "templates" / "secret_raw.docx").write_text("реквизиты\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "code.py", "templates/keep.docx")
    return tmp_path


def _guard():
    from build_integrity_manifest import ignored_by_git

    return ignored_by_git


def test_ignored_file_is_reported(repo: Path) -> None:
    """Главный случай: файл есть на диске, но git его игнорирует — значит на
    свежей установке его не будет, и манифест сделает приложение кирпичом."""
    assert _guard()(repo, ["code.py", "templates/secret_raw.docx"]) == ["templates/secret_raw.docx"]


def test_ignored_file_not_last_in_list_is_reported(repo: Path) -> None:
    """Порядок не должен ничего решать, а решал: первая версия склеивала пути
    через \\n, и на Windows текстовый режим подставлял \\r\\n. Все имена, кроме
    последнего, приезжали в git с \\r на конце — то есть проверялись не те
    пути. Тест с единственным игнорируемым файлом В КОНЦЕ этого не замечал."""
    got = _guard()(repo, ["templates/secret_raw.docx", "code.py"])
    assert got == ["templates/secret_raw.docx"]


def test_file_restored_by_negation_rule_is_not_reported(repo: Path) -> None:
    """Каталог закрыт целиком, файл возвращён правилом «!». Он в git есть, и
    объявить его игнорируемым значит запретить сборку манифеста на ровном
    месте. Ровно это и произошло с letterhead.docx."""
    assert _guard()(repo, ["templates/keep.docx"]) == []


def test_tracked_files_are_not_reported(repo: Path) -> None:
    assert _guard()(repo, ["code.py", ".gitignore"]) == []


def test_new_file_awaiting_git_add_is_not_reported(repo: Path) -> None:
    """Не игнорируемый, просто ещё не добавленный файл — обычная середина
    работы. Ругаться на неё значило бы приучить пропускать предупреждение."""
    (repo / "новый_модуль.py").write_text("Y = 2\n", encoding="utf-8")
    assert _guard()(repo, ["новый_модуль.py"]) == []


def test_empty_list_is_not_a_question_to_git(repo: Path) -> None:
    assert _guard()(repo, []) == []


def test_outside_git_repo_returns_none_not_empty(tmp_path: Path) -> None:
    """None и [] — разные ответы. «Проверить нечем» нельзя показывать как
    «всё чисто»: иначе предохранитель молча выключается там, где git нет."""
    assert _guard()(tmp_path, ["code.py"]) is None
