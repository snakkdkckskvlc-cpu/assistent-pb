"""Сбой поиска не должен убивать всю задачу — но и молчать о нём нельзя.

Повод не гипотетический. На живой базе повреждённая коллекция отвечала
ошибкой «Error executing plan: Error finding id» на ЛЮБОЙ запрос с фильтром
отменённых редакций, хотя тот же запрос без фильтра отрабатывал. Вызов
retrieve_many в pipelines/legal.py не обёрнут в try, поэтому пользователь
получал не «анализ без ссылок на нормативку», а полное падение задачи.

Отсюда два требования, и они тянут в разные стороны:

1. Сбой ретривера не должен ронять анализ договора — деградируем до пустой
   выдачи.
2. Деградация обязана быть ВИДНОЙ. Молча выдать разбор договора без единой
   ссылки на закон — это ровно та тихая деградация, которой в этом проекте
   уже дорого обошлась пропажа фирменного бланка.
"""

from __future__ import annotations

import pytest
from fire_safety_rag import retriever


class _BrokenCollection:
    """Коллекция, которая существует и непуста, но падает на запросе."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or RuntimeError("Error executing plan: Error finding id")
        self.calls = 0

    def count(self) -> int:
        return 3742

    def query(self, **kwargs) -> dict:
        self.calls += 1
        raise self._error


class _WorkingCollection:
    def count(self) -> int:
        return 2

    def query(self, *, query_texts, n_results, where=None) -> dict:
        n = len(query_texts)
        return {
            "documents": [["passage: текст нормы"] for _ in range(n)],
            "metadatas": [[{"source": "СП 7", "status": "actual"}] for _ in range(n)],
            "distances": [[0.1] for _ in range(n)],
        }


@pytest.fixture(autouse=True)
def _clean_failure():
    retriever._search_failure = ""
    yield
    retriever._search_failure = ""


def _retriever_with(collection) -> retriever.Retriever:
    r = retriever.Retriever.__new__(retriever.Retriever)
    r._collection = collection
    return r


# --- Деградация ---


def test_search_returns_empty_instead_of_raising() -> None:
    """Иначе повреждённый индекс означает не «анализ без нормативки», а
    отсутствие анализа вовсе."""
    r = _retriever_with(_BrokenCollection())
    assert r.search("противодымная вентиляция") == []


def test_search_many_degrades_to_empty_per_query() -> None:
    """Форма ответа обязана сохраниться: вызывающий код разбирает список по
    индексам запросов."""
    r = _retriever_with(_BrokenCollection())
    assert r.search_many(["первый", "второй", "третий"]) == [[], [], []]


def test_broken_index_does_not_look_ready_to_the_interface() -> None:
    r = _retriever_with(_BrokenCollection())
    r.search("вентиляция")
    assert "Error finding id" in retriever.search_failure()


# --- Видимость ---


def test_failure_is_recorded_with_the_reason() -> None:
    r = _retriever_with(_BrokenCollection(ValueError("метаданные не читаются")))
    r.search("вентиляция")
    failure = retriever.search_failure()
    assert "ValueError" in failure
    assert "метаданные не читаются" in failure


def test_no_failure_recorded_when_everything_works() -> None:
    r = _retriever_with(_WorkingCollection())
    assert len(r.search("вентиляция")) == 1
    assert retriever.search_failure() == ""


def test_successful_search_clears_previous_failure() -> None:
    """Починили индекс — интерфейс не должен продолжать пугать пользователя."""
    retriever._search_failure = "старая ошибка"
    r = _retriever_with(_WorkingCollection())
    r.search("вентиляция")
    assert retriever.search_failure() == ""


# --- Что деградация не сломала ---


def test_working_search_still_returns_hits() -> None:
    r = _retriever_with(_WorkingCollection())
    hits = r.search("вентиляция")
    assert hits[0]["source"] == "СП 7"
    # Служебный префикс эмбеддера снимается — в промпт модели он не нужен.
    assert hits[0]["text"] == "текст нормы"


def test_not_ready_retriever_does_not_touch_the_collection() -> None:
    """Пустая база — это не сбой, и ошибку записывать не за что."""
    r = _retriever_with(None)
    assert r.search("вентиляция") == []
    assert retriever.search_failure() == ""
