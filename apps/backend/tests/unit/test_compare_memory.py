"""Память подтверждённых соответствий: что она обязана и чего не должна.

Тесты держат не «сохраняется ли пара» — это очевидно, — а свойства, нарушение
которых портит сверку молча: цепочки, переписывание чужого соответствия и
отсутствие отзыва.

Фикстура `client` обязательна: без неё записи уедут в настоящую data/app.db.
На этом я уже попался с тестами ролей.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.services import compare_memory as memory

СМЕТА = "Кабель ВВГнг 3х1,5"
АКТ = "Кабель ВВГ нг 3*1.5"
# Нормализованные ключи этих написаний. Здесь они литералами, а не вызовом
# normalize: хранилище про движок ничего не знает и знать не должно, а тест,
# который тянет чужой модуль ради двух строк, ломается вместе с ним.
КЛЮЧ_СМЕТА = "кабель ввгнг 3x1.5"
КЛЮЧ_АКТ = "кабель ввг нг 3x1.5"


def test_pair_is_remembered_and_given_to_the_engine(client: TestClient) -> None:
    memory.remember(КЛЮЧ_АКТ, КЛЮЧ_СМЕТА, name_from=АКТ, name_to=СМЕТА, by="ivanov")
    assert memory.synonyms() == {КЛЮЧ_АКТ: КЛЮЧ_СМЕТА}


def test_engine_matches_silently_the_second_time(client: TestClient) -> None:
    """Ради этого всё и делается: подтвердил один раз — дальше молча.

    Единственный тест, которому нужен сам движок сверки. Он пишется
    параллельно, в другой сессии, и до его коммита тест пропускается: держать
    сборку красной из-за чужого незакоммиченного модуля нельзя, а выбрасывать
    проверку главного свойства — тем более.
    """
    engine = pytest.importorskip(
        "fire_safety_backend.services.table_compare",
        reason="движок сверки таблиц ещё не в репозитории",
    )
    # Количество в тысячных, сумма в копейках — движок держит всё целыми.
    слева = [engine.Row(номер=1, название=СМЕТА, количество=10_000, сумма=100_000)]
    справа = [engine.Row(номер=1, название=АКТ, количество=10_000, сумма=100_000)]

    без_памяти = engine.compare(слева, справа)
    assert not без_памяти.сошлось, "без памяти позиции не должны склеиться сами"

    memory.remember(engine.normalize(АКТ), engine.normalize(СМЕТА), name_from=АКТ, name_to=СМЕТА)
    с_памятью = engine.compare(слева, справа, синонимы=memory.synonyms())
    assert len(с_памятью.сошлось) == 1
    assert not с_памятью.только_слева and not с_памятью.только_справа


def test_confirming_the_same_pair_twice_is_not_an_error(client: TestClient) -> None:
    """Человек мог забыть, что уже подтверждал. Наказывать за это нечем."""
    first = memory.remember("а", "б")
    assert memory.remember("а", "б") == first
    assert len(memory.list_pairs()) == 1


def test_same_key_cannot_be_pointed_elsewhere(client: TestClient) -> None:
    """Молча переписать прежнее соответствие нельзя: прошлые сверки поедут."""
    memory.remember("а", "б")
    with pytest.raises(ValueError, match="уже сведена"):
        memory.remember("а", "в")
    assert memory.synonyms() == {"а": "б"}


def test_chain_forward_is_rejected(client: TestClient) -> None:
    """A→B и B→C: результат начал бы зависеть от порядка применения."""
    memory.remember("а", "б")
    with pytest.raises(memory.ChainNotAllowed):
        memory.remember("б", "в")


def test_chain_backward_is_rejected(client: TestClient) -> None:
    """Обратный конец той же цепочки: C→A, когда уже есть A→B."""
    memory.remember("а", "б")
    with pytest.raises(memory.ChainNotAllowed):
        memory.remember("в", "а")


def test_self_pair_is_rejected(client: TestClient) -> None:
    with pytest.raises(ValueError, match="сама с собой"):
        memory.remember("а", "а")


def test_empty_key_is_rejected(client: TestClient) -> None:
    with pytest.raises(ValueError, match="Пустой ключ"):
        memory.remember("  ", "б")


def test_pair_can_be_revoked(client: TestClient) -> None:
    """Ошибочная пара иначе живёт вечно и тихо портит каждую сверку."""
    pair_id = memory.remember("а", "б")
    assert memory.forget(pair_id) is True
    assert memory.synonyms() == {}
    assert memory.forget(pair_id) is False


def test_list_keeps_original_spelling(client: TestClient) -> None:
    """По нормализованному ключу человек свою позицию не узнает."""
    memory.remember(КЛЮЧ_АКТ, КЛЮЧ_СМЕТА, name_from=АКТ, name_to=СМЕТА, by="ivanov")
    pair = memory.list_pairs()[0]
    assert pair["name_from"] == АКТ
    assert pair["name_to"] == СМЕТА
    assert pair["confirmed_by"] == "ivanov"
