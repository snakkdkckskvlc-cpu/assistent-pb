"""Журнал прохождения документов.

Проверяется прежде всего то, ради чего он заводится: что время считается САМО
и считается верно. Журнал, который показывает неправильные сроки, хуже
отсутствия журнала — по нему будут принимать решения.

Второй пласт — машина состояний. Ошибка здесь тихая: документ уходит в
состояние, из которого нет выхода, или закрытый продолжает висеть у человека
в списке «у меня».
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fire_safety_backend.infrastructure import db as db_module
from fire_safety_backend.services import doc_flow


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "flow.db")
    db_module.init_db()


def _счёт(**kw) -> int:
    params = {
        "kind": "счёт",
        "number": "СЧ-15",
        "counterparty": "ООО «Кабель-Сервис»",
        "amount_kop": 6288000,
        "holder": "snab",
        "author": "snab",
    }
    params.update(kw)
    return doc_flow.create(**params)


def _сдвинуть(doc_id: int, **сдвиги: float) -> None:
    """Отодвигает события документа в прошлое.

    Иначе всё происходит в одну секунду, и любая проверка сроков сводится к
    нулю — то есть не проверяет ничего.
    """
    with db_module.connect() as conn:
        строки = conn.execute(
            "SELECT id FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        часы = сдвиги.get("часов_назад", 0)
        шаг = сдвиги.get("шаг_часов", 0)
        for i, r in enumerate(строки):
            момент = datetime.now() - timedelta(hours=часы - i * шаг)
            conn.execute(
                "UPDATE doc_flow_event SET at = ? WHERE id = ?",
                (момент.isoformat(sep=" ", timespec="seconds"), r["id"]),
            )


# --- ради чего всё это: где документ и сколько он там ---


def test_document_shows_where_it_is(_db: None) -> None:
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab", note="на визу")
    doc = doc_flow.get(doc_id)
    assert doc["holder"] == "findir"
    assert doc["state"] == doc_flow.В_РАБОТЕ
    assert [e["action"] for e in doc["история"]] == ["заведён", "передан"]


def test_days_counted_from_last_move_not_creation(_db: None) -> None:
    """Счёт, прошедший четыре руки, не должен показывать «лежит 12 дней» у
    человека, к которому попал утром."""
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    # заведён 100 часов назад, передан — 2 часа назад
    with db_module.connect() as conn:
        события = conn.execute(
            "SELECT id FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        for r, часы in zip(события, (100, 2), strict=True):
            conn.execute(
                "UPDATE doc_flow_event SET at = ? WHERE id = ?",
                (
                    (datetime.now() - timedelta(hours=часы)).isoformat(sep=" ", timespec="seconds"),
                    r["id"],
                ),
            )
    assert doc_flow.get(doc_id)["дней_у_текущего"] == pytest.approx(2 / 24, abs=0.05)
    assert doc_flow.search(holder="findir")[0]["дней_у_текущего"] == pytest.approx(2 / 24, abs=0.05)


def test_timing_report_is_the_missing_row_of_the_map(_db: None) -> None:
    """Та самая пустая строка «Время протекания процесса» — посчитанная сама."""
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАВИЗИРОВАН, actor="findir")
    doc_flow.hand_over(doc_id, to="buh", actor="findir")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="buh", note="оплачен")
    # события через каждые 6 часов, начиная 30 часов назад
    _сдвинуть(doc_id, часов_назад=30, шаг_часов=6)

    отчёт = doc_flow.timing()
    у_кого = {r["кто"]: r for r in отчёт["у_кого"]}
    assert "findir" in у_кого, "должно быть видно, сколько счёт лежал у финдиректора"
    assert у_кого["findir"]["медиана_часов"] > 0


def test_whole_document_time_reconciles_with_per_person(_db: None) -> None:
    """Одни часы, а не двое.

    Время у людей считается из событий, и время документа целиком обязано
    считаться оттуда же — иначе цифры на одном экране не сходятся между собой,
    и доверять нельзя ни одной. Раньше документ целиком брался из
    created_at/closed_at: проверка в браузере показала «шёл 0 ч» у документа,
    который на самом деле шёл почти четверо суток.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.hand_over(doc_id, to="buh", actor="findir")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="buh", note="оплачен")
    # события через каждые 10 часов, начиная 40 часов назад: всего 30 часов пути
    _сдвинуть(doc_id, часов_назад=40, шаг_часов=10)

    отчёт = doc_flow.timing()
    целиком = отчёт["по_видам"][0]["медиана_часов"]
    сумма_по_людям = sum(r["медиана_часов"] * r["передач"] for r in отчёт["у_кого"])
    assert целиком == pytest.approx(30, abs=0.5)
    assert сумма_по_людям == pytest.approx(целиком, abs=0.5), "части обязаны складываться в целое"


def test_state_change_does_not_split_a_stay(_db: None) -> None:
    """Виза не разрывает пребывание документа у человека.

    Наивный счёт «от события до следующего» резал пребывание там, где никто
    ничего не передавал: финдиректор, у которого счёт пролежал 50 часов и был
    завизирован за 2 часа до передачи, получал два отрезка и медиану 25 вместо
    честных 50 — отчёт занижал ровно то место, ради обнаружения которого он и
    делается.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАВИЗИРОВАН, actor="findir")
    doc_flow.hand_over(doc_id, to="buh", actor="findir")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="buh", note="оплачен")
    # заведён -96, передан findir -90, виза -42, передан buh -40, закрыт -12
    with db_module.connect() as conn:
        строки = conn.execute(
            "SELECT id FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        for r, ч in zip(строки, (96, 90, 42, 40, 12), strict=True):
            conn.execute(
                "UPDATE doc_flow_event SET at = ? WHERE id = ?",
                (
                    (datetime.now() - timedelta(hours=ч)).isoformat(sep=" ", timespec="seconds"),
                    r["id"],
                ),
            )

    у_кого = {r["кто"]: r for r in doc_flow.timing()["у_кого"]}
    assert у_кого["findir"]["передач"] == 1, "одно пребывание, а не два"
    assert у_кого["findir"]["медиана_часов"] == pytest.approx(50, abs=0.5)
    assert у_кого["snab"]["медиана_часов"] == pytest.approx(6, abs=0.5)
    assert у_кого["buh"]["медиана_часов"] == pytest.approx(28, abs=0.5)


def test_open_interval_not_counted(_db: None) -> None:
    """Документ, который всё ещё у человека, в статистику не берётся: отрезок
    растёт каждую секунду и портил бы медиану."""
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    у_кого = {r["кто"]: r for r in doc_flow.timing()["у_кого"]}
    assert "findir" not in у_кого
    assert "snab" in у_кого, "закрытый отрезок у первого держателя есть"


# --- машина состояний ---


def test_closed_document_has_no_holder(_db: None) -> None:
    """Закрытый ни у кого не лежит — иначе он вечно висел бы в чьём-то «у меня»."""
    doc_id = _счёт()
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    doc = doc_flow.get(doc_id)
    assert doc["holder"] == ""
    assert doc["closed_at"]
    assert doc_flow.search(holder="snab") == []


@pytest.mark.parametrize(
    ("из", "в"),
    [
        (doc_flow.ЗАКРЫТ, doc_flow.В_РАБОТЕ),
        (doc_flow.ЗАКРЫТ, doc_flow.ЗАВИЗИРОВАН),
        (doc_flow.ЗАВИЗИРОВАН, doc_flow.В_РАБОТЕ),
    ],
)
def test_forbidden_transitions(_db: None, из: str, в: str) -> None:
    doc_id = _счёт()
    if из != doc_flow.В_РАБОТЕ:
        doc_flow.change_state(doc_id, to=из, actor="x", note="причина")
    with pytest.raises(doc_flow.FlowError, match="нельзя перейти"):
        doc_flow.change_state(doc_id, to=в, actor="x", note="причина")


def test_rejected_goes_back_to_work(_db: None) -> None:
    """Отклонённый правят и пускают по кругу заново — это нормальный путь."""
    doc_id = _счёт()
    doc_flow.change_state(doc_id, to=doc_flow.ОТКЛОНЁН, actor="findir", note="нет договора")
    doc_flow.change_state(doc_id, to=doc_flow.В_РАБОТЕ, actor="snab", note="договор приложен")
    assert doc_flow.get(doc_id)["state"] == doc_flow.В_РАБОТЕ


def test_rejection_requires_a_reason(_db: None) -> None:
    """Отклонённый без причины возвращается тому же человеку, который не знает,
    что исправлять, и круг повторяется — ровно та потеря времени, ради которой
    журнал заводится."""
    doc_id = _счёт()
    with pytest.raises(doc_flow.FlowError, match="причина"):
        doc_flow.change_state(doc_id, to=doc_flow.ОТКЛОНЁН, actor="findir")
    with pytest.raises(doc_flow.FlowError, match="причина"):
        doc_flow.change_state(doc_id, to=doc_flow.ОТКЛОНЁН, actor="findir", note="   ")


def test_closed_cannot_be_handed_over(_db: None) -> None:
    doc_id = _счёт()
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    with pytest.raises(doc_flow.FlowError, match="закрыт"):
        doc_flow.hand_over(doc_id, to="findir", actor="snab")


def test_hand_over_to_self_is_refused(_db: None) -> None:
    """Пустое движение засоряет историю и портит счёт времени."""
    doc_id = _счёт()
    with pytest.raises(doc_flow.FlowError, match="и так у"):
        doc_flow.hand_over(doc_id, to="snab", actor="snab")


# --- заведение и поиск ---


def test_unknown_kind_is_refused(_db: None) -> None:
    """Свободный ввод вида превращает отчёт по времени в мусор, где «счёт»,
    «Счет» и «счёт на оплату» — три разных потока."""
    with pytest.raises(doc_flow.FlowError, match="вид"):
        _счёт(kind="счет на оплату")


def test_holder_is_required(_db: None) -> None:
    with pytest.raises(doc_flow.FlowError, match="у кого"):
        _счёт(holder="  ")


def test_search_hides_closed_by_default(_db: None) -> None:
    открытый = _счёт(number="СЧ-1")
    закрытый = _счёт(number="СЧ-2")
    doc_flow.change_state(закрытый, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    номера = [d["number"] for d in doc_flow.search()]
    assert номера == ["СЧ-1"]
    assert открытый  # использован
    assert len(doc_flow.search(state=doc_flow.ЗАКРЫТ)) == 1


def test_search_by_text(_db: None) -> None:
    _счёт(number="СЧ-15", counterparty="ООО «Кабель-Сервис»")
    _счёт(number="СЧ-16", counterparty="ООО «Труба»")
    assert len(doc_flow.search(text="Кабель")) == 1
    assert len(doc_flow.search(text="СЧ-1")) == 2


def test_overdue(_db: None) -> None:
    вчера = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    завтра = (datetime.now() + timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    просроченный = _счёт(number="СЧ-СРОК", due_at=вчера)
    _счёт(number="СЧ-ОК", due_at=завтра)
    assert [d["number"] for d in doc_flow.search(overdue=True)] == ["СЧ-СРОК"]
    assert doc_flow.get(просроченный)["просрочен"] is True


def test_closed_document_is_never_overdue(_db: None) -> None:
    """Закрытый вчерашним сроком уже никого не касается."""
    вчера = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    doc_id = _счёт(due_at=вчера)
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    assert doc_flow.get(doc_id)["просрочен"] is False
    assert doc_flow.search(overdue=True, state=doc_flow.ЗАКРЫТ) == []


def test_negative_amount_is_refused(_db: None) -> None:
    with pytest.raises(doc_flow.FlowError, match="отрицательной"):
        _счёт(amount_kop=-1)


def test_missing_document(_db: None) -> None:
    with pytest.raises(LookupError):
        doc_flow.get(9999)


# --- находки состязательной проверки ---


def test_visa_does_not_reset_the_counter(_db: None) -> None:
    """Отметка «завизирован» не обнуляет «сколько уже лежит».

    Документ пролежал трое суток, финдиректор поставил визу и не передал — на
    экране показывалось «только что». Ровно то место, ради обнаружения
    которого экран и сделан, переставало быть видно.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАВИЗИРОВАН, actor="findir")
    with db_module.connect() as conn:
        строки = conn.execute(
            "SELECT id FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        for r, ч in zip(строки, (100, 72, 1), strict=True):
            conn.execute(
                "UPDATE doc_flow_event SET at = ? WHERE id = ?",
                (
                    (datetime.now() - timedelta(hours=ч)).isoformat(sep=" ", timespec="seconds"),
                    r["id"],
                ),
            )
    # держатель не менялся с отметки -72 ч, значит три дня, а не «час назад»
    assert doc_flow.get(doc_id)["дней_у_текущего"] == pytest.approx(3, abs=0.1)
    assert doc_flow.search(holder="findir")[0]["дней_у_текущего"] == pytest.approx(3, abs=0.1)


def test_closed_document_counter_is_zero(_db: None) -> None:
    doc_id = _счёт()
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    assert doc_flow.get(doc_id)["дней_у_текущего"] == 0.0


def test_overdue_filter_runs_before_limit(_db: None) -> None:
    """Просроченные — самые СТАРЫЕ документы.

    Раньше отбор шёл в Python после выборки: бралось N последних, и уже из них
    отсеивались просроченные. При живом журнале вкладка показывала «ничего не
    найдено» ровно тогда, когда просрочено было больше всего.
    """
    вчера = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    старый_просроченный = _счёт(number="СТАРЫЙ", due_at=вчера)
    for i in range(10):
        _счёт(number=f"СВЕЖИЙ-{i}")
    найдено = doc_flow.search(overdue=True, limit=3)
    assert [d["number"] for d in найдено] == ["СТАРЫЙ"]
    assert старый_просроченный  # использован


def test_hand_over_loses_race_to_close(_db: None) -> None:
    """Гонка передачи и закрытия не должна дать закрытый документ с держателем.

    Тридцать сотрудников на один сервер: между чтением и записью документ
    успевает изменить другой человек. Проверяем подменой состояния между
    чтением и записью — так же, как это выглядит при настоящей гонке.
    """
    doc_id = _счёт()
    настоящий_row = doc_flow._row

    def подменённый(conn, did):
        строка = настоящий_row(conn, did)
        # Кто-то успел закрыть документ ровно сейчас — ОТДЕЛЬНЫМ подключением.
        # Внутри той же транзакции подмена откатилась бы вместе с ошибкой, и
        # тест проверял бы сам себя, а не замок.
        with db_module.connect() as другой:
            другой.execute(
                "UPDATE doc_flow SET state = ?, holder = '' WHERE id = ?", (doc_flow.ЗАКРЫТ, did)
            )
        return строка

    doc_flow._row = подменённый
    try:
        with pytest.raises(doc_flow.FlowError, match="только что изменили"):
            doc_flow.hand_over(doc_id, to="findir", actor="snab")
    finally:
        doc_flow._row = настоящий_row

    итог = doc_flow.get(doc_id)
    assert итог["state"] == doc_flow.ЗАКРЫТ
    assert итог["holder"] == "", "закрытый документ не может остаться у человека"


def test_limit_cannot_be_bypassed(_db: None) -> None:
    for i in range(5):
        _счёт(number=f"Д-{i}")
    assert len(doc_flow.search(limit=-1)) == 1, "отрицательный предел не должен отдавать всё"
    assert len(doc_flow.search(limit=10_000)) == 5
