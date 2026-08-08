"""Тесты предохранителей автообновления.

Почему именно здесь нужны тесты: check_and_apply_update() выполняет
`git reset --hard`, то есть безвозвратно затирает несохранённую работу в
папке, на которую его натравили. Единственное, что отделяет пользователя
(и разработчика, у которого проект лежит в такой же папке) от потери
изменений — функция _is_safe_to_update(). Ошибка в ней не приведёт к
падению или заметному сбою: обновление просто молча снесёт правки.

Поэтому тесты гоняют настоящий git на временных репозиториях, а не мокают
subprocess: проверять надо фактическое поведение git, а не наши
представления о нём.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from fire_safety_desktop import updater

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git не установлен")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_origin(tmp_path: Path) -> Path:
    """«Удалённый» репозиторий с одним коммитом."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--initial-branch", "main")
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "app.txt").write_text("v1", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "--quiet", "-m", "v1")
    return origin


def _clone(origin: Path, dest: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(dest)], check=True, capture_output=True
    )
    _git(dest, "config", "user.email", "test@example.com")
    _git(dest, "config", "user.name", "Test")
    return dest


def _advance_origin(origin: Path) -> None:
    """Новый коммит в origin — установка становится отстающей."""
    (origin / "app.txt").write_text("v2", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "--quiet", "-m", "v2")


@pytest.fixture
def clean_install(tmp_path: Path) -> tuple[Path, Path]:
    origin = _make_origin(tmp_path)
    work = _clone(origin, tmp_path / "install")
    return origin, work


# --- _is_safe_to_update: собственно предохранитель ---


def test_clean_main_is_safe(clean_install: tuple[Path, Path]) -> None:
    _, work = clean_install
    assert updater._is_safe_to_update(work) is True


def test_uncommitted_changes_block_update(clean_install: tuple[Path, Path]) -> None:
    """Самый важный случай: у разработчика есть незакоммиченная работа."""
    _, work = clean_install
    (work / "app.txt").write_text("моя несохранённая правка", encoding="utf-8")
    assert updater._is_safe_to_update(work) is False


def test_untracked_file_blocks_update(clean_install: tuple[Path, Path]) -> None:
    """Новый неотслеживаемый файл — тоже чужая работа, git reset её снесёт."""
    _, work = clean_install
    (work / "черновик.txt").write_text("не потерять", encoding="utf-8")
    assert updater._is_safe_to_update(work) is False


def test_other_branch_blocks_update(clean_install: tuple[Path, Path]) -> None:
    """Не main — почти наверняка ветка разработчика, а не установка."""
    _, work = clean_install
    _git(work, "checkout", "--quiet", "-b", "feature")
    assert updater._is_safe_to_update(work) is False


def test_not_a_git_repo_is_not_safe(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert updater._is_safe_to_update(plain) is False


# --- check_and_apply_update: сценарии целиком ---


def test_up_to_date_does_nothing(clean_install: tuple[Path, Path]) -> None:
    _, work = clean_install
    assert updater.check_and_apply_update(work) is False


def test_missing_git_dir_does_nothing(tmp_path: Path) -> None:
    plain = tmp_path / "no-git"
    plain.mkdir()
    assert updater.check_and_apply_update(plain) is False


def test_env_var_disables_update(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, work = clean_install
    _advance_origin(origin)
    monkeypatch.setenv("ASSISTENT_PB_DISABLE_AUTOUPDATE", "1")
    assert updater.check_and_apply_update(work) is False
    assert (work / "app.txt").read_text(encoding="utf-8") == "v1"


def test_dirty_install_behind_remote_keeps_local_work(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключевой сценарий потери данных: есть и обновление, и своя работа.

    Обновление обязано отступить, а правка — уцелеть. Если этот тест
    когда-нибудь покраснеет, значит автообновление начало съедать
    несохранённые изменения.
    """
    origin, work = clean_install
    _advance_origin(origin)
    (work / "app.txt").write_text("важная несохранённая работа", encoding="utf-8")

    # Переустановка зависимостей и переиндексация к предохранителю отношения
    # не имеют, но они медленные и лезут в сеть — глушим, чтобы тест проверял
    # ровно защиту.
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: True)

    assert updater.check_and_apply_update(work) is False
    assert (work / "app.txt").read_text(encoding="utf-8") == "важная несохранённая работа"


def test_clean_install_behind_remote_updates(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обратная сторона: на чистой установке обновление обязано применяться."""
    origin, work = clean_install
    _advance_origin(origin)
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: True)

    assert updater.check_and_apply_update(work) is True
    assert (work / "app.txt").read_text(encoding="utf-8") == "v2"


def test_update_applies_even_if_reindex_fails(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой переиндексации не должен выглядеть как «обновления не было».

    Код уже обновлён и перезапуск нужен; молчаливый False привёл бы к тому,
    что приложение осталось бы работать со старым кодом в памяти.
    """
    origin, work = clean_install
    _advance_origin(origin)
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: False)

    assert updater.check_and_apply_update(work) is True


def test_no_network_does_not_break_startup(clean_install: tuple[Path, Path]) -> None:
    """Офлайн — штатный режим этого приложения: молча работаем на старом."""
    _, work = clean_install
    # Недостижимый remote — git fetch падает ровно так же, как без сети.
    _git(work, "remote", "set-url", "origin", "https://127.0.0.1:1/nope.git")
    assert updater.check_and_apply_update(work) is False
    assert (work / "app.txt").read_text(encoding="utf-8") == "v1"


# --- Незапушенные коммиты: близкий промах, из-за которого правка и появилась ---


def test_unpushed_commits_block_update(clean_install: tuple[Path, Path]) -> None:
    """Работа закоммичена, дерево чистое, ветка main — и раньше этого хватало,
    чтобы reset --hard стёр все локальные коммиты молча.

    Реальный случай: 10 коммитов работы уцелели только потому, что приложение
    в тот день не запускали.
    """
    origin, work = clean_install
    _advance_origin(origin)
    (work / "моя_работа.txt").write_text("важное", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "--quiet", "-m", "локальный коммит")
    _git(work, "fetch", "--quiet", "origin", "main")

    assert updater._is_safe_to_update(work) is False


def test_local_commits_survive_the_update_attempt(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сквозная проверка: попытка обновления не должна тронуть локальную работу."""
    # Как и в соседних сквозных тестах: pip и переиндексация медленные и лезут
    # в сеть. Здесь это ещё и защита от самого теста — если предохранитель
    # сломается, тест не должен уйти в реальный pip install на 600 секунд
    # против рабочего интерпретатора, он должен упасть на ассерте.
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: True)
    origin, work = clean_install
    _advance_origin(origin)
    (work / "моя_работа.txt").write_text("важное", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "--quiet", "-m", "локальный коммит")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert updater.check_and_apply_update(work) is False

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before, "локальный коммит не должен исчезнуть"
    assert (work / "моя_работа.txt").exists()


def test_unresolvable_remote_ref_blocks_update(clean_install: tuple[Path, Path]) -> None:
    """Не смогли выяснить, есть ли локальные коммиты, — считаем небезопасным.

    Раньше пустой вывод `rev-list --count` трактовался как «ноль коммитов
    впереди», то есть предохранитель открывался ровно тогда, когда выяснить
    ничего не удалось.
    """
    _, work = clean_install
    _git(work, "update-ref", "-d", "refs/remotes/origin/main")
    assert updater._is_safe_to_update(work) is False


def test_stale_remote_ref_does_not_pass_off_local_commits_as_synced(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сверка коммитов обязана идти ПОСЛЕ fetch.

    Если считать по устаревшей ссылке, локальный коммит выглядит как «уже в
    origin» (ссылка указывает на него же), и reset --hard его стирает. Тест
    ловит именно перестановку шагов: fetch внутри check_and_apply_update
    обновляет ссылку, и коммит становится виден.
    """
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: True)
    origin, work = clean_install

    # Локальный коммит, о котором устаревшая ссылка origin/main не знает.
    (work / "работа.txt").write_text("важное", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "--quiet", "-m", "локальное")
    # origin тем временем ушёл вперёд своим путём — обновление «доступно».
    _advance_origin(origin)

    assert updater.check_and_apply_update(work) is False
    assert (work / "работа.txt").exists(), "локальная работа не должна исчезнуть"


def test_dirty_tree_does_not_touch_the_network(
    clean_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Грязное дерево — ответ известен без сети, fetch делать незачем.

    Иначе каждый запуск приложения у разработчика ждёт до FETCH_TIMEOUT_SEC
    впустую.
    """
    _, work = clean_install
    (work / "app.txt").write_text("правка", encoding="utf-8")

    calls: list[tuple[str, ...]] = []
    real_git = updater._git

    def spy(root, *args, **kwargs):
        calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(updater, "_git", spy)
    assert updater.check_and_apply_update(work) is False
    assert not any(a and a[0] == "fetch" for a in calls), "fetch не должен вызываться"


# --- пропуск переустановки на документационных обновлениях ---
#
# Замерено на истории репозитория: только 8 коммитов из 114 (7 %) трогают
# исключительно документацию. В плане стояло «44 из 111» — там посчитали
# коммиты, ЗАТРАГИВАЮЩИЕ документацию (их 49), но 41 из них правит её вместе
# с кодом, и такие обязаны проходить полный цикл. Выигрыш скромнее
# ожидаемого, поэтому тем важнее, чтобы предохранитель не срабатывал лишний
# раз: цена ошибки — ImportError на тридцати машинах.


def _advance_origin_with(origin: Path, *rel_paths: str) -> None:
    """Коммит в origin, трогающий ровно перечисленные файлы."""
    for rel in rel_paths:
        target = origin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("изменено", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "--quiet", "-m", "правка")


@pytest.fixture
def spy_apply(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Следит, дошло ли дело до переустановки и переиндексации."""
    done: list[str] = []
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda root: done.append("pip") or True)
    monkeypatch.setattr(updater, "_reindex_corpus", lambda root: done.append("reindex") or True)
    return done


# --- предохранитель ОБЯЗАН срабатывать ---


@pytest.mark.parametrize(
    "path",
    [
        "docs/02-product/roadmap.md",
        "docs/03-architecture/adr/0004-rag-chromadb.md",
        "README.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        ".claude/rules/model.md",
        ".github/workflows/ci.yml",
        "LICENSE",
    ],
)
def test_docs_only_update_skips_everything(
    clean_install: tuple[Path, Path], spy_apply: list[str], path: str
) -> None:
    """Ни pip, ни переиндексации, ни перезапуска: исполняемого кода не тронуто.

    False здесь означает не «не обновилось» — git reset уже прошёл и файлы на
    диске новые, — а «перезапускать нечего»: main.py по True показывает диалог
    и перезапускает окно, и ради правки README это лишнее.
    """
    origin, work = clean_install
    _advance_origin_with(origin, path)

    assert updater.check_and_apply_update(work) is False
    assert spy_apply == [], f"{path} не должен вызывать переустановку"
    # но обновление применено: файл на диске появился
    assert (work / path).exists(), "reset --hard обязан был доехать до диска"


def test_several_docs_files_at_once_still_skip(
    clean_install: tuple[Path, Path], spy_apply: list[str]
) -> None:
    origin, work = clean_install
    _advance_origin_with(origin, "docs/README.md", "CHANGELOG.md", ".claude/hooks/charcheck.py")
    assert updater.check_and_apply_update(work) is False
    assert spy_apply == []


# --- предохранитель ОБЯЗАН НЕ срабатывать ---


@pytest.mark.parametrize(
    ("path", "why"),
    [
        (
            "packages/rag/corpus/nlmk/README.md",
            "документ корпуса: без переиндексации доедет файлом, но не фактом для поиска",
        ),
        (
            "packages/rag/corpus/123-FZ.txt",
            "тоже корпус — тот случай, ради которого существует _reindex_corpus",
        ),
        ("apps/backend/src/fire_safety_backend/main.py", "исполняемый код"),
        ("requirements-runtime.txt", "новая зависимость — без pip будет ImportError"),
        ("apps/backend/pyproject.toml", "точки входа и состав пакета"),
        ("install/README.md", "markdown не в корне: правило про .md умышленно узкое"),
        (".gitignore", "в нём пофайлово перечислен корпус, CLAUDE.md §4.5"),
        ("apps/desktop/frontend/app.js", "фронтенд тоже поставляется пользователю"),
    ],
)
def test_non_docs_update_runs_full_apply(
    clean_install: tuple[Path, Path], spy_apply: list[str], path: str, why: str
) -> None:
    origin, work = clean_install
    _advance_origin_with(origin, path)
    assert updater.check_and_apply_update(work) is True, why
    assert spy_apply == ["pip", "reindex"], why


def test_docs_together_with_code_runs_full_apply(
    clean_install: tuple[Path, Path], spy_apply: list[str]
) -> None:
    """Самый частый случай в этом репозитории: 41 коммит из 114.

    Достаточно одного неинертного файла в коммите, чтобы цикл прошёл целиком.
    """
    origin, work = clean_install
    _advance_origin_with(origin, "docs/README.md", "apps/backend/src/fire_safety_backend/db.py")
    assert updater.check_and_apply_update(work) is True
    assert spy_apply == ["pip", "reindex"]


def test_unreadable_diff_falls_back_to_full_apply(
    clean_install: tuple[Path, Path], spy_apply: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не смогли выяснить список изменений — делаем всё.

    Та же логика несимметричной цены, что в _is_safe_to_update: непонятное
    считается требующим полной обработки, а не пропуска.
    """
    origin, work = clean_install
    _advance_origin_with(origin, "docs/README.md")
    monkeypatch.setattr(updater, "_changed_paths", lambda root, before: None)
    assert updater.check_and_apply_update(work) is True
    assert spy_apply == ["pip", "reindex"]


# --- сам предикат, без git ---


def test_inert_path_predicate() -> None:
    assert updater._is_inert_path("docs/anything/deep.md")
    assert updater._is_inert_path("README.md")
    assert updater._is_inert_path("LICENSE")
    assert updater._is_inert_path(".claude/settings.json")

    assert not updater._is_inert_path("packages/rag/corpus/nlmk/README.md")
    assert not updater._is_inert_path("scripts/index_corpus.py")
    assert not updater._is_inert_path("docs.py"), "имя файла, а не каталог docs/"
    assert not updater._is_inert_path("documentation/x.md"), "префикс должен быть точным"
