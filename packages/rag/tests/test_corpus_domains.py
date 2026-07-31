"""Разделение корпуса на домены: нормативка РФ и документы заказчика.

У них разный статус: СТО НЛМК — не источник права, ссылаться на него в
юридическом анализе как на норму нельзя. Поэтому разные коллекции, и главное,
что здесь проверяется, — что они не перетекают друг в друга.
"""

from __future__ import annotations

import pytest
from fire_safety_rag import config
from fire_safety_rag.indexer import _domain_files


def test_known_domains_map_to_distinct_collections() -> None:
    pb = config.collection_for_domain("pb")
    nlmk = config.collection_for_domain("nlmk")
    assert pb != nlmk


def test_pb_keeps_the_existing_collection_name() -> None:
    """Регрессия на молчаливую поломку: в рабочем индексе лежит legal_corpus
    на 3334 чанка. Переименование домена «pb» осиротило бы его — ретривер
    вернул бы пустоту без единой ошибки."""
    assert config.collection_for_domain("pb") == config.COLLECTION_NAME


def test_no_domain_means_normative_corpus() -> None:
    assert config.collection_for_domain(None) == config.COLLECTION_NAME
    assert config.collection_for_domain() == config.COLLECTION_NAME


def test_unknown_domain_raises_instead_of_silently_defaulting() -> None:
    """Опечатка в --domain иначе проиндексировала бы документы не в ту
    коллекцию, и заметили бы это только по пустой выдаче."""
    with pytest.raises(ValueError, match="Неизвестный домен"):
        config.collection_for_domain("nmlk")


def test_nlmk_documents_live_in_a_subdirectory() -> None:
    assert config.corpus_dir_for_domain("nlmk") == config.CORPUS_DIR / config.NLMK_CORPUS_SUBDIR
    assert config.corpus_dir_for_domain("pb") == config.CORPUS_DIR


def test_normative_indexing_skips_customer_documents(tmp_path) -> None:
    """Ключевая проверка. Обход корпуса рекурсивный, и без явного исключения
    документы заказчика из corpus/nlmk попали бы в нормативную коллекцию."""
    (tmp_path / "123-FZ.txt").write_text("Статья 1.", encoding="utf-8")
    (tmp_path / "_meta.json").write_text("{}", encoding="utf-8")
    nlmk = tmp_path / config.NLMK_CORPUS_SUBDIR
    nlmk.mkdir()
    (nlmk / "STO_NLMK.pdf").write_bytes(b"x")

    pb_files = {p.name for p in _domain_files(tmp_path, "pb")}
    assert pb_files == {"123-FZ.txt"}, "СТО заказчика не должен попасть в нормативку"

    nlmk_files = {p.name for p in _domain_files(nlmk, "nlmk")}
    assert nlmk_files == {"STO_NLMK.pdf"}


def test_sidecar_and_hidden_files_are_skipped(tmp_path) -> None:
    (tmp_path / "law.txt").write_text("Статья 1.", encoding="utf-8")
    (tmp_path / "_meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    assert {p.name for p in _domain_files(tmp_path, "pb")} == {"law.txt"}


def test_nested_normative_subfolders_are_still_indexed(tmp_path) -> None:
    """Исключается только чужой домен, а не вложенность вообще."""
    sub = tmp_path / "sp"
    sub.mkdir()
    (sub / "SP5.txt").write_text("5.1. Текст", encoding="utf-8")
    assert {p.name for p in _domain_files(tmp_path, "pb")} == {"SP5.txt"}
