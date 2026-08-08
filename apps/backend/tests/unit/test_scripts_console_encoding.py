"""Скрипты не должны падать на кодовой странице русской консоли.

Как это ломалось. На русской Windows stdout отдаётся в cp1251 (или cp866), а
скрипты печатают «→», «—», «≈» и кавычки-ёлочки. Один такой символ роняет
скрипт с UnicodeEncodeError — причём ДО полезной работы: `check_corpus.py`
падал на одиннадцатом символе первой строки, не дойдя до самой проверки.

Ошибка при этом указывает на charmap и кодовую страницу, то есть выглядит как
поломка окружения, а не как «в строке лишняя стрелка». Человек, запустивший
команду из документации, остаётся ни с чем.

Лечится одним местом — `scripts/_venv.py::use_utf8_console`, который зовут все
скрипты (кто-то через `ensure_venv`, кто-то напрямую).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"

# Кодовые страницы, в которых Windows отдаёт stdout при русской локали: 866 —
# кодировка КОНСОЛИ по умолчанию, 1251 — системная ANSI (в неё попадает
# перенаправленный вывод). Масштаб замерен: в cp1251 не кодируются 8 скриптов
# из 17, а в cp866 — ВСЕ СЕМНАДЦАТЬ. То есть на обычной русской консоли падал
# любой скрипт проекта, а не «некоторые».
_CONSOLE_ENCODINGS = ("cp866", "cp1251")


def _load_venv_module():
    sys.path.insert(0, str(_SCRIPTS))
    import _venv

    return _venv


def test_use_utf8_console_switches_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    venv = _load_venv_module()
    calls: list[dict] = []

    class _Stream:
        def reconfigure(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())
    venv.use_utf8_console()

    assert len(calls) == 2, "переключить надо оба потока, ошибки идут в stderr"
    for kwargs in calls:
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"


def test_stream_without_reconfigure_is_survived(monkeypatch: pytest.MonkeyPatch) -> None:
    """StringIO вместо потока — обычное дело в тестах, падать тут нельзя."""
    venv = _load_venv_module()
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    venv.use_utf8_console()  # не должно бросить


def test_closed_stream_is_survived(monkeypatch: pytest.MonkeyPatch) -> None:
    venv = _load_venv_module()

    class _Broken:
        def reconfigure(self, **kwargs) -> None:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys, "stdout", _Broken())
    monkeypatch.setattr(sys, "stderr", _Broken())
    venv.use_utf8_console()  # не должно бросить


def _risky_scripts() -> list[Path]:
    """Скрипты, чей текст не влезает в кодировку консоли.

    Критерий по всему исходнику, а не только по строкам вывода: это
    приближение в СТРОГУЮ сторону (комментарий с «—» сам по себе не печатается)
    и потому безопасное — лишний вызов `use_utf8_console` ничего не стоит, а
    пропущенный роняет скрипт у пользователя.
    """
    out = []
    for path in sorted(_SCRIPTS.glob("*.py")):
        if path.name == "_venv.py":
            continue
        text = path.read_text(encoding="utf-8")
        for encoding in _CONSOLE_ENCODINGS:
            try:
                text.encode(encoding)
            except UnicodeEncodeError:
                out.append(path)
                break
    return out


@pytest.mark.parametrize("script", _risky_scripts(), ids=lambda p: p.name)
def test_risky_script_is_protected(script: Path) -> None:
    """Печатаешь стрелку — обеспечь кодировку.

    Годится любой из двух способов: `ensure_venv()` (он зовёт переключение
    сам) или прямой вызов `use_utf8_console()` в самостоятельных скриптах,
    которые venv проекта не требуют.
    """
    source = script.read_text(encoding="utf-8")
    assert "ensure_venv()" in source or "use_utf8_console()" in source, (
        f"{script.name} печатает символы вне cp1251, но не включает UTF-8 — "
        f"на русской консоли он упадёт до полезной работы"
    )


def test_the_guard_actually_covers_something() -> None:
    """Список «рискованных» не должен опустеть молча.

    Замерено на момент правки: под критерий попадают ВСЕ скрипты проекта,
    потому что русские кавычки-ёлочки и длинное тире есть в комментариях
    почти везде, а в cp866 их нет.
    """
    risky = _risky_scripts()
    all_scripts = [p for p in _SCRIPTS.glob("*.py") if p.name != "_venv.py"]
    assert len(risky) == len(all_scripts), (
        "если хоть один скрипт вышел из-под критерия — проверьте, не сузился "
        "ли список кодировок консоли"
    )
