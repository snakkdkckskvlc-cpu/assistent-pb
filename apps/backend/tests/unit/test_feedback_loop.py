"""Фидбек-луп: сохранение неудачного ответа и сборка негативных примеров.

Смысл цепочки: комментарий «плохо разобрал ответственность» сам по себе ни к
чему не привязан — через месяц по нему нечего разбирать. Ценна связка «что
пользователь считает неправильным» + «что модель на самом деле выдала».
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fire_safety_backend.infrastructure import db as db_module
from fire_safety_backend.models import FeedbackCreate
from fire_safety_backend.services import feedback as service

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "update_prompts_from_feedback.py"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("upd_prompts", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["upd_prompts"] = module
    spec.loader.exec_module(module)
    return module


def _payload(rating: str = "down", comment: str = "перепутал стороны") -> FeedbackCreate:
    return FeedbackCreate(function="legal", task_id="t1", rating=rating, comment=comment)


# --- Сохранение ответа модели -----------------------------------------------


def test_bad_output_is_saved_for_thumbs_down_with_comment() -> None:
    service.create(_payload(), {"находки": [{"в_чём_риск": "неверно"}]})
    rows = service.list_negative()
    assert len(rows) == 1
    assert "неверно" in rows[0]["bad_output"]
    assert rows[0]["comment"] == "перепутал стороны"


def test_bad_output_is_not_saved_for_thumbs_up() -> None:
    """Для 👍 разбирать нечего, а результат раздует базу."""
    service.create(_payload(rating="up", comment="отлично"), {"находки": ["x"]})
    with db_module.connect() as conn:
        row = conn.execute("SELECT bad_output FROM feedback").fetchone()
    assert row["bad_output"] == ""


def test_bad_output_is_not_saved_without_a_comment() -> None:
    """«Плохо» без пояснения не превратить в правило для промпта."""
    service.create(_payload(comment=""), {"находки": ["x"]})
    with db_module.connect() as conn:
        row = conn.execute("SELECT bad_output FROM feedback").fetchone()
    assert row["bad_output"] == ""
    assert service.list_negative() == []


def test_huge_output_is_truncated() -> None:
    """Разбор договора — десятки килобайт; целиком в базу отзывов он не нужен."""
    service.create(_payload(), {"текст": "я" * 50000})
    stored = service.list_negative()[0]["bad_output"]
    assert len(stored) < 21000
    assert stored.endswith("(обрезано)")


def test_list_negative_filters_by_function() -> None:
    service.create(_payload(), {"a": 1})
    service.create(
        FeedbackCreate(function="letter", task_id="t2", rating="down", comment="сухо"),
        {"b": 2},
    )
    assert len(service.list_negative(function="legal")) == 1
    assert len(service.list_negative()) == 2


def test_unserializable_output_does_not_break_saving() -> None:
    service.create(_payload(), object())
    assert service.list_negative()[0]["bad_output"]


# --- Сборка блока негативных примеров ---------------------------------------


def test_block_contains_complaint_and_excerpt(script) -> None:
    block, used = script._build_block(
        [{"comment": "перепутал стороны договора", "bad_output": "Подрядчик вправе не платить"}]
    )
    assert len(used) == 1
    assert "перепутал стороны договора" in block
    assert "Подрядчик вправе не платить" in block


def test_block_respects_the_size_cap(script) -> None:
    """Каждый токен промпта отнимается у текста договора — потолок жёсткий."""
    many = [{"comment": "замечание " * 20, "bad_output": "плохой ответ " * 40} for _ in range(50)]
    block, used = script._build_block(many)
    assert len(block) <= script._MAX_BLOCK_CHARS
    assert 0 < len(used) < 50, "часть отзывов обязана не поместиться"


def test_entries_without_comment_are_skipped(script) -> None:
    block, used = script._build_block([{"comment": "   ", "bad_output": "что-то"}])
    assert used == []
    assert block == ""


def test_excerpt_is_shortened_and_single_line(script) -> None:
    excerpt = script._excerpt("строка\nвторая\n\n" + "х" * 500)
    assert "\n" not in excerpt
    assert len(excerpt) <= script._MAX_EXCERPT_CHARS + 1


# --- Подклейка к промпту ----------------------------------------------------


def test_load_prompt_appends_negative_examples(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from fire_safety_backend import config as config_module
    from fire_safety_backend.pipelines import _prompts

    monkeypatch.setattr(config_module, "PROMPTS_DIR", tmp_path)
    (tmp_path / "legal.txt").write_text("Основной промпт.", encoding="utf-8")
    assert _prompts.load_prompt("legal") == "Основной промпт."

    (tmp_path / "legal_negative.txt").write_text("НЕ НАДО: врать.", encoding="utf-8")
    combined = _prompts.load_prompt("legal")
    assert combined.startswith("Основной промпт.")
    assert "НЕ НАДО: врать." in combined


def test_empty_negative_file_changes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from fire_safety_backend import config as config_module
    from fire_safety_backend.pipelines import _prompts

    monkeypatch.setattr(config_module, "PROMPTS_DIR", tmp_path)
    (tmp_path / "legal.txt").write_text("Основной промпт.", encoding="utf-8")
    (tmp_path / "legal_negative.txt").write_text("   \n", encoding="utf-8")
    assert _prompts.load_prompt("legal") == "Основной промпт."


def test_migration_adds_column_to_an_existing_old_database(tmp_path) -> None:
    """Регрессия на живой прогон: CREATE TABLE IF NOT EXISTS существующую
    таблицу не трогает, и на рабочей базе со старой схемой запрос падал на
    отсутствующем столбце bad_output."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, function TEXT NOT NULL, "
        "task_id TEXT NOT NULL, rating TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '');"
        "INSERT INTO feedback (function, task_id, rating, comment) "
        "VALUES ('legal', 't0', 'down', 'старый отзыв');"
    )
    conn.commit()
    conn.close()

    original = db_module.DB_PATH
    try:
        db_module.DB_PATH = path
        db_module.init_db()
        with db_module.connect() as c:
            columns = {r["name"] for r in c.execute("PRAGMA table_info(feedback)")}
            kept = c.execute("SELECT comment FROM feedback").fetchone()["comment"]
    finally:
        db_module.DB_PATH = original

    assert "bad_output" in columns
    assert kept == "старый отзыв", "миграция не должна терять существующие записи"
