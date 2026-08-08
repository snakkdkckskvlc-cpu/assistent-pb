"""Журнал прохождения документов.

Проверяется прежде всего то, ради чего он заводится: что время считается САМО
и считается верно. Журнал, который показывает неправильные сроки, хуже
отсутствия журнала — по нему будут принимать решения.

Второй пласт — машина состояний. Ошибка здесь тихая: документ уходит в
состояние, из которого нет выхода, или закрытый продолжает висеть у человека
в списке «у меня».
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
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


def _в_моменты(doc_id: int, моменты: list[datetime]) -> None:
    """Ставит события документа в ЗАДАННЫЕ моменты.

    Сдвиг «на N часов назад» перестал годиться, когда отчёт начал считать
    рабочие часы: тот же сдвиг даёт разный результат в зависимости от того, в
    какое время суток и в какой день недели запущен тест. Плавающий тест не
    проверяет ничего.
    """
    with db_module.connect() as conn:
        строки = conn.execute(
            "SELECT id FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        for r, м in zip(строки, моменты, strict=True):
            conn.execute(
                "UPDATE doc_flow_event SET at = ? WHERE id = ?",
                (м.isoformat(sep=" ", timespec="seconds"), r["id"]),
            )


def _последний_будний(час: int, минута: int = 0, назад_дней: int = 0) -> datetime:
    """Недавний будний день в заданное время — чтобы попадать в рабочие часы."""
    д = datetime.now().date() - timedelta(days=назад_дней)
    while д.weekday() >= 5:
        д -= timedelta(days=1)
    return datetime.combine(д, time(час, минута))


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

    отчёт = doc_flow.timing(with_people=True)
    у_кого = {r["кто"]: r for r in отчёт["у_кого"]}
    assert "findir" in у_кого, "должно быть видно, сколько счёт лежал у финдиректора"
    assert у_кого["findir"]["медиана_часов"] > 0


def test_whole_document_time_reconciles_with_per_person(_db: None) -> None:
    """Одни часы, а не двое: части обязаны складываться в целое.

    Время у людей и время документа целиком считаются из одного источника —
    журнала событий. Раньше документ целиком брался из created_at/closed_at, и
    проверка в браузере показала «шёл 0 ч» у документа, который шёл четверо
    суток.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.hand_over(doc_id, to="buh", actor="findir")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="buh", note="оплачен")
    д = _последний_будний(9, назад_дней=1)
    _в_моменты(doc_id, [д, д + timedelta(hours=1), д + timedelta(hours=3), д + timedelta(hours=6)])

    отчёт = doc_flow.timing(with_people=True)
    целиком = отчёт["по_видам"][0]["медиана_часов"]
    сумма_по_людям = sum(r["медиана_часов"] * r["передач"] for r in отчёт["у_кого"])
    assert целиком == pytest.approx(
        doc_flow.рабочих_часов(д.isoformat(sep=" "), (д + timedelta(hours=6)).isoformat(sep=" ")),
        abs=0.05,
    )
    assert сумма_по_людям == pytest.approx(целиком, abs=0.05), "части обязаны складываться в целое"


def test_state_change_does_not_split_a_stay(_db: None) -> None:
    """Виза не разрывает пребывание документа у человека.

    Наивный счёт «от события до следующего» резал пребывание там, где никто
    ничего не передавал: финдиректор с семью часами получал два отрезка и
    медиану вдвое меньше — отчёт занижал ровно то место, ради обнаружения
    которого он делается.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАВИЗИРОВАН, actor="findir")
    doc_flow.hand_over(doc_id, to="buh", actor="findir")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="buh", note="оплачен")
    д = _последний_будний(9, назад_дней=1)
    пришёл, виза, ушёл = д + timedelta(hours=1), д + timedelta(hours=3), д + timedelta(hours=8)
    _в_моменты(doc_id, [д, пришёл, виза, ушёл, ушёл + timedelta(minutes=1)])

    у_кого = {r["кто"]: r for r in doc_flow.timing(with_people=True)["у_кого"]}
    ожидалось = doc_flow.рабочих_часов(пришёл.isoformat(sep=" "), ушёл.isoformat(sep=" "))
    assert у_кого["findir"]["передач"] == 1, "одно пребывание, а не два"
    assert у_кого["findir"]["медиана_часов"] == pytest.approx(ожидалось, abs=0.05)
    # При разрыве получились бы два куска по 2 и 5 часов с медианой около 3 —
    # проверка ниже отсекает именно этот случай.
    assert у_кого["findir"]["медиана_часов"] > 5


def test_open_interval_not_counted(_db: None) -> None:
    """Документ, который всё ещё у человека, в статистику не берётся: отрезок
    растёт каждую секунду и портил бы медиану."""
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    д = _последний_будний(9, назад_дней=1)
    _в_моменты(doc_id, [д, д + timedelta(hours=3)])
    у_кого = {r["кто"]: r for r in doc_flow.timing(with_people=True)["у_кого"]}
    assert "findir" not in у_кого, "документ всё ещё у него — отрезок открыт"
    assert "snab" in у_кого, "закрытый отрезок у первого держателя есть"


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
    with pytest.raises(doc_flow.FlowError, match="не подходит|уже закрыт|Виза уже стоит"):
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
    with pytest.raises(doc_flow.FlowError, match="вид документа"):
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
        with pytest.raises(doc_flow.FlowError, match="только что изменил"):
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


def test_per_person_timing_is_opt_in(_db: None) -> None:
    """Поимённый рейтинг медлительности не отдаётся по умолчанию.

    Экран «Сколько идут» существует, чтобы видеть, где встаёт ПОТОК, и причина
    чаще не в человеке, а в том, что до него документ дошёл поздно. Открывать
    такой рейтинг всем тридцати — наблюдение за коллегами, а не рабочая
    информация. То же решение, что на экране «Что происходит».
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="findir", note="оплачен")

    обычному = doc_flow.timing()
    assert "у_кого" not in обычному
    assert "по_видам" in обычному, "сроки по видам документов видны всем"
    assert "findir" not in repr(обычному)

    администратору = doc_flow.timing(with_people=True)
    assert "у_кого" in администратору


def test_long_stay_started_before_window_is_not_lost(_db: None) -> None:
    """Отрезок, начавшийся до границы окна, обязан попасть в отчёт.

    Раньше события отбирались по at >= порог, и счёт, попавший к финдиректору
    за день до границы и пролежавший две недели, не давал ни одного отрезка —
    то есть самое долгое залёживание в отчёт не попадало вовсе.
    """
    doc_id = _счёт()
    doc_flow.hand_over(doc_id, to="findir", actor="snab")
    doc_flow.change_state(doc_id, to=doc_flow.ЗАКРЫТ, actor="findir", note="оплачен")
    пришёл = _последний_будний(10, назад_дней=41)
    закрыт = _последний_будний(10, назад_дней=1)
    _в_моменты(doc_id, [пришёл - timedelta(hours=1), пришёл, закрыт])

    у_кого = {r["кто"]: r for r in doc_flow.timing(30, with_people=True)["у_кого"]}
    assert "findir" in у_кого, "залёживание длиной сорок дней не должно пропасть"
    ожидалось = doc_flow.рабочих_часов(пришёл.isoformat(sep=" "), закрыт.isoformat(sep=" "))
    assert у_кого["findir"]["медиана_часов"] == pytest.approx(ожидалось, abs=0.05)
    assert у_кого["findir"]["медиана_часов"] > 100, "это очень долгое залёживание"


def test_instant_registration_does_not_drag_the_median(_db: None) -> None:
    """«Завёл и сразу передал» — не пребывание, а след регистрации.

    Такие нули тянули медиану секретаря вниз и делали её неотличимой от нуля у
    того, кто и правда держит документы днями.
    """
    for i in range(4):
        d = _счёт(number=f"СЧ-{i}", holder="secretary", author="secretary")
        doc_flow.hand_over(d, to="snab", actor="secretary")  # мгновенно, тот же миг
        doc_flow.change_state(d, to=doc_flow.ЗАКРЫТ, actor="snab", note="оплачен")
    у_кого = {r["кто"]: r for r in doc_flow.timing(with_people=True)["у_кого"]}
    assert "secretary" not in у_кого, "мгновенная регистрация — не пребывание"


def test_forgotten_document_comes_first(_db: None) -> None:
    """Забытый документ обязан быть сверху, а не тонуть под свежими.

    Экран существует ровно для того, чтобы такие находились: при сортировке по
    свежести записи забытый счёт уходил в самый низ и при обрезке списка
    исчезал совсем.
    """
    старый = _счёт(number="ЗАБЫТЫЙ")
    with db_module.connect() as conn:
        conn.execute(
            "UPDATE doc_flow_event SET at = ? WHERE doc_id = ?",
            ((datetime.now() - timedelta(days=21)).isoformat(sep=" ", timespec="seconds"), старый),
        )
    for i in range(3):
        _счёт(number=f"СВЕЖИЙ-{i}")
    assert doc_flow.search()[0]["number"] == "ЗАБЫТЫЙ"


# --- рабочие часы: арифметика отдельно от журнала ---


def _мск(день: str, час: int, минута: int = 0) -> str:
    """Момент по дате вида «2026-08-10» (это понедельник)."""
    return f"{день} {час:02d}:{минута:02d}:00"


def test_working_hours_within_one_day() -> None:
    """Полный рабочий день — восемь часов: с 8:30 до 17:30 минус час обеда."""
    assert doc_flow.рабочих_часов(_мск("2026-08-10", 8, 30), _мск("2026-08-10", 17, 30)) == 8.0


def test_night_is_not_counted() -> None:
    """Счёт, отданный в 17:00 и завизированный утром, лежал полтора часа, а не
    шестнадцать."""
    часы = doc_flow.рабочих_часов(_мск("2026-08-10", 17, 0), _мск("2026-08-11", 9, 0))
    assert часы == pytest.approx(0.44 + 0.44, abs=0.15), f"получилось {часы}"
    assert часы < 2


def test_weekend_is_not_counted() -> None:
    """Пятница 17:00 → понедельник 9:00.

    По календарю это 64 часа, и требовать за них объяснений было бы неправдой:
    контора не работала. Ради этого отчёт и переведён на рабочее время — карта
    процессов меряет то же самое (Пн–Пт, 8:30–17:30, час обеда).
    """
    # 2026-08-14 — пятница, 2026-08-17 — понедельник
    часы = doc_flow.рабочих_часов(_мск("2026-08-14", 17, 0), _мск("2026-08-17", 9, 0))
    assert часы < 2, f"выходные не считаются, получилось {часы}"


def test_whole_working_week() -> None:
    """Понедельник 8:30 → пятница 17:30 — сорок рабочих часов."""
    assert doc_flow.рабочих_часов(_мск("2026-08-10", 8, 30), _мск("2026-08-14", 17, 30)) == 40.0


def test_evening_and_early_morning_are_outside() -> None:
    assert doc_flow.рабочих_часов(_мск("2026-08-10", 18, 0), _мск("2026-08-10", 23, 0)) == 0.0
    assert doc_flow.рабочих_часов(_мск("2026-08-10", 3, 0), _мск("2026-08-10", 7, 0)) == 0.0


def test_segments_are_additive() -> None:
    """Сумма кусков равна целому — иначе части и целое на экране не сойдутся."""
    целиком = doc_flow.рабочих_часов(_мск("2026-08-10", 9, 0), _мск("2026-08-12", 16, 0))
    части = doc_flow.рабочих_часов(
        _мск("2026-08-10", 9, 0), _мск("2026-08-11", 11, 0)
    ) + doc_flow.рабочих_часов(_мск("2026-08-11", 11, 0), _мск("2026-08-12", 16, 0))
    assert части == pytest.approx(целиком, abs=0.05)


def test_backwards_and_broken_input() -> None:
    assert doc_flow.рабочих_часов(_мск("2026-08-11", 9, 0), _мск("2026-08-10", 9, 0)) == 0.0
    assert doc_flow.рабочих_часов("не дата", _мск("2026-08-10", 9, 0)) is None


# --- документ у уволенного ---


def test_document_with_a_gone_holder_is_flagged(_db: None) -> None:
    """Документ у уволенного не всплывает нигде: он не в чьём-то «у меня», и
    вопрос «где счёт» снова остаётся без ответа."""
    from fire_safety_backend.services import auth as auth_service

    auth_service.create_user("ivanov")
    auth_service.create_user("uvolen")
    doc_id = _счёт(holder="ivanov", author="ivanov")
    потерянный = _счёт(number="СЧ-99", holder="uvolen", author="ivanov")
    auth_service.set_disabled("uvolen", True)

    по_номерам = {d["number"]: d for d in doc_flow.search()}
    assert по_номерам["СЧ-99"]["держатель_потерян"] is True
    assert по_номерам["СЧ-15"]["держатель_потерян"] is False
    assert doc_id and потерянный
