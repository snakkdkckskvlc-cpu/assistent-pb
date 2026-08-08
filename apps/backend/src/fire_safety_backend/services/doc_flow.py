"""Журнал прохождения документов: где счёт, у кого лежит и сколько уже.

Первый этап проекта CRM (docs/03-architecture/crm-target-design.md, §8) и
функция №3 каталога. Взят первым не потому, что самый заметный, а потому, что
он одновременно инструмент и ИЗМЕРИТЕЛЬ: в карте информационного потока строка
«Время протекания процесса» пуста, и пока она пуста, любую следующую функцию
приходится приоритизировать на ощупь, а эффект — обещать, а не показывать.
Как только документы заводятся сюда, время считается из движений само.

Состояние документа выводится из событий, а не хранится отдельно от них.
Хранить только текущего держателя дешевле, но тогда «сколько счёт пролежал у
финдиректора» посчитать нечем — а это половина смысла.

Маршрут НЕ жёсткий. Карта описывает только благополучный путь, а в жизни счёт
возвращают, документ уходит не тому и приносят обратно. Жёсткий маршрут в такой
ситуации либо блокирует работу, либо обходится мимо системы — и журнал начинает
врать. Поэтому передать можно кому угодно, а порядок обеспечивается тем, что
всё видно.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..infrastructure.db import connect

# Виды документов, которые реально ходят по рукам в этой компании — из карты
# информационного потока. Список закрытый: свободный ввод вида превращает
# отчёт по времени в мусор, где «счёт», «Счет» и «счёт на оплату» — три разных
# потока.
KINDS: tuple[tuple[str, str], ...] = (
    ("счёт", "Счёт на оплату"),
    ("накладная", "Накладная"),
    ("акт", "Акт выполненных работ"),
    ("закрывающие", "Закрывающие документы"),
    ("договор", "Договор"),
    ("письмо", "Письмо"),
    ("заявка", "Заявка на материал"),
    ("прочее", "Прочее"),
)
_KIND_CODES = {code for code, _ in KINDS}

В_РАБОТЕ = "в работе"
ЗАВИЗИРОВАН = "завизирован"
ОТКЛОНЁН = "отклонён"
ЗАКРЫТ = "закрыт"

# Куда из какого состояния можно. Закрытый документ — конец: если бы из него
# был выход, «сколько шёл документ» перестало бы иметь смысл, а история
# превратилась бы в бесконечную ленту.
ПЕРЕХОДЫ: dict[str, frozenset[str]] = {
    В_РАБОТЕ: frozenset({ЗАВИЗИРОВАН, ОТКЛОНЁН, ЗАКРЫТ}),
    ЗАВИЗИРОВАН: frozenset({ЗАКРЫТ, ОТКЛОНЁН}),
    # Отклонённый возвращается в работу: его правят и пускают по кругу заново.
    ОТКЛОНЁН: frozenset({В_РАБОТЕ, ЗАКРЫТ}),
    ЗАКРЫТ: frozenset(),
}

ФИНАЛЬНЫЕ = frozenset({ЗАКРЫТ})


class FlowError(ValueError):
    """Действие невозможно в текущем состоянии документа."""


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _row(conn, doc_id: int) -> dict:
    row = conn.execute("SELECT * FROM doc_flow WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise LookupError("Документ не найден")
    return dict(row)


def _log(conn, doc_id: int, *, actor: str, action: str, было: dict, стало: dict, note: str) -> None:
    conn.execute(
        "INSERT INTO doc_flow_event "
        "(doc_id, at, actor, action, from_holder, to_holder, from_state, to_state, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id,
            _now(),
            actor,
            action,
            было.get("holder", ""),
            стало.get("holder", ""),
            было.get("state", ""),
            стало.get("state", ""),
            note.strip(),
        ),
    )


def create(
    *,
    kind: str,
    number: str = "",
    counterparty: str = "",
    subject: str = "",
    amount_kop: int | None = None,
    due_at: str | None = None,
    holder: str,
    author: str,
) -> int:
    """Заводит документ. Возвращает его номер в журнале.

    `holder` — у кого документ окажется сразу. Обычно это сам заводящий, но не
    всегда: секретарь регистрирует входящий счёт и сразу передаёт снабженцу.
    """
    if kind not in _KIND_CODES:
        raise FlowError(f"Неизвестный вид документа: {kind}")
    if not str(holder).strip():
        raise FlowError("Нужно указать, у кого документ")
    if amount_kop is not None and amount_kop < 0:
        raise FlowError("Сумма не может быть отрицательной")

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO doc_flow "
            "(kind, number, counterparty, subject, amount_kop, state, holder, due_at, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kind,
                number.strip(),
                counterparty.strip(),
                subject.strip(),
                amount_kop,
                В_РАБОТЕ,
                holder.strip(),
                due_at,
                author,
                _now(),
            ),
        )
        doc_id = int(cur.lastrowid)
        _log(
            conn,
            doc_id,
            actor=author,
            action="заведён",
            было={},
            стало={"holder": holder.strip(), "state": В_РАБОТЕ},
            note="",
        )
    return doc_id


def hand_over(doc_id: int, *, to: str, actor: str, note: str = "") -> None:
    """Передать документ другому человеку."""
    to = str(to).strip()
    if not to:
        raise FlowError("Нужно указать, кому передаём")
    with connect() as conn:
        было = _row(conn, doc_id)
        if было["state"] in ФИНАЛЬНЫЕ:
            raise FlowError("Документ закрыт — передавать его больше некому")
        if было["holder"] == to:
            raise FlowError(f"Документ и так у «{to}»")
        # Условие в WHERE, а не только проверка выше: между чтением и записью
        # тот же документ мог закрыть или передать другой человек — тридцать
        # сотрудников на один сервер. Без этого получался закрытый документ с
        # держателем, то есть состояние, из которого нет выхода.
        cur = conn.execute(
            "UPDATE doc_flow SET holder = ? WHERE id = ? AND state = ? AND holder = ?",
            (to, doc_id, было["state"], было["holder"]),
        )
        if cur.rowcount == 0:
            raise FlowError("Документ только что изменили — откройте его заново")
        _log(
            conn,
            doc_id,
            actor=actor,
            action="передан",
            было=было,
            стало={"holder": to, "state": было["state"]},
            note=note,
        )


def change_state(doc_id: int, *, to: str, actor: str, note: str = "") -> None:
    """Сменить состояние: завизировать, отклонить, закрыть, вернуть в работу.

    Отклонение обязано нести причину. Отклонённый без объяснения документ
    возвращается тому же человеку, который не знает, что исправлять, и круг
    повторяется — ровно та потеря времени, ради которой журнал и заводится.
    """
    if to not in ПЕРЕХОДЫ:
        raise FlowError(f"Неизвестное состояние: {to}")
    if to == ОТКЛОНЁН and not note.strip():
        raise FlowError("У отклонения должна быть причина — иначе непонятно, что исправлять")

    with connect() as conn:
        было = _row(conn, doc_id)
        if to not in ПЕРЕХОДЫ[было["state"]]:
            raise FlowError(f"Из состояния «{было['state']}» нельзя перейти в «{to}»")

        # У закрытого документа держателя нет: он ни у кого не лежит, и
        # показывать его в чьём-то списке «у меня» было бы неправдой.
        holder = "" if to in ФИНАЛЬНЫЕ else было["holder"]
        closed_at = _now() if to in ФИНАЛЬНЫЕ else None
        # Тот же замок, что в hand_over, и по той же причине. Дополнительно
        # holder переписывается ТОЛЬКО при закрытии: раньше он записывался
        # всегда, и одновременная передача молча откатывалась — таблица
        # расходилась с историей, где передача осталась записанной.
        if to in ФИНАЛЬНЫЕ:
            cur = conn.execute(
                "UPDATE doc_flow SET state = ?, holder = '', closed_at = ? "
                "WHERE id = ? AND state = ?",
                (to, closed_at, doc_id, было["state"]),
            )
        else:
            cur = conn.execute(
                "UPDATE doc_flow SET state = ? WHERE id = ? AND state = ?",
                (to, doc_id, было["state"]),
            )
        if cur.rowcount == 0:
            raise FlowError("Документ только что изменили — откройте его заново")
        _log(
            conn,
            doc_id,
            actor=actor,
            action=to,
            было=было,
            стало={"holder": holder, "state": to},
            note=note,
        )


def get(doc_id: int) -> dict:
    """Документ вместе с историей движений."""
    with connect() as conn:
        doc = _row(conn, doc_id)
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM doc_flow_event WHERE doc_id = ? ORDER BY id", (doc_id,)
            )
        ]
    doc["история"] = events
    doc["дней_у_текущего"] = _дней_у_держателя(doc, events)
    doc["просрочен"] = _просрочен(doc)
    return doc


def _дней_у_держателя(doc: dict, events: list[dict]) -> float:
    """Сколько документ лежит у НЫНЕШНЕГО держателя.

    Считается от события, которым он к нему попал, а не от любого последнего.
    Иначе отметка «завизирован» обнуляет счётчик: документ пролежал три дня,
    финдиректор поставил визу и не передал — на экране «только что». Ровно то
    место, ради обнаружения которого экран и сделан, переставало быть видно.

    У закрытого документа держателя нет, и счётчик обязан быть нулём: иначе он
    продолжает расти у документа, который никого больше не касается.
    """
    if doc["state"] in ФИНАЛЬНЫЕ or not doc.get("holder"):
        return 0.0
    for e in reversed(events):
        if e["from_holder"] != e["to_holder"]:
            return _дней_с(e["at"])
    return _дней_с(doc["created_at"])


def _дней_с(момент: str) -> float:
    try:
        начало = datetime.fromisoformat(момент)
    except (TypeError, ValueError):
        return 0.0
    # Отрицательное — это часы, переведённые назад, или запись задним числом.
    # Показывать «минус два дня» человеку нельзя, и в статистику такое тоже не
    # берётся (см. timing).
    return max(0.0, round((datetime.now() - начало).total_seconds() / 86400, 1))


def _просрочен(doc: dict) -> bool:
    if doc["state"] in ФИНАЛЬНЫЕ or not doc.get("due_at"):
        return False
    try:
        return datetime.fromisoformat(str(doc["due_at"])) < datetime.now()
    except ValueError:
        return False


def search(
    *,
    holder: str | None = None,
    state: str | None = None,
    kind: str | None = None,
    text: str = "",
    overdue: bool = False,
    limit: int = 200,
) -> list[dict]:
    """Список документов под фильтры. Без фильтров — всё открытое."""
    sql = "SELECT * FROM doc_flow WHERE 1=1"
    params: list = []
    if holder:
        sql += " AND holder = ?"
        params.append(holder)
    if state:
        sql += " AND state = ?"
        params.append(state)
    else:
        # По умолчанию закрытые не показываем: журнал нужен для того, что ещё
        # в работе, а закрытых со временем станет во много раз больше.
        sql += " AND state <> ?"
        params.append(ЗАКРЫТ)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if text.strip():
        sql += " AND (number LIKE ? OR counterparty LIKE ? OR subject LIKE ?)"
        шаблон = f"%{text.strip()}%"
        params += [шаблон, шаблон, шаблон]
    if overdue:
        # Отбор просроченных ДО LIMIT, а не после.
        #
        # Раньше фильтр стоял в Python после выборки: бралось двести последних
        # документов, и уже из них отсеивались просроченные. При живом журнале
        # просроченные — самые СТАРЫЕ, то есть в двести последних не попадают, и
        # вкладка показывала «ничего не найдено» ровно тогда, когда просрочено
        # было больше всего. Молчаливая деградация в чистом виде.
        sql += " AND due_at IS NOT NULL AND due_at < ? AND state <> ?"
        params += [_now(), ЗАКРЫТ]
        # Просроченные сортируются от самых давних: их и надо разбирать первыми.
        sql += " ORDER BY due_at ASC LIMIT ?"
    else:
        sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))

    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
        # Момент, когда документ попал к нынешнему держателю. Берём событие,
        # МЕНЯВШЕЕ держателя: отметка «завизирован» держателя не меняет и
        # счётчик обнулять не должна.
        последние = {
            r["doc_id"]: r["at"]
            for r in conn.execute(
                "SELECT doc_id, MAX(at) AS at FROM doc_flow_event "
                "WHERE from_holder <> to_holder GROUP BY doc_id"
            )
        }
    for doc in rows:
        doc["дней_у_текущего"] = (
            0.0
            if doc["state"] in ФИНАЛЬНЫЕ or not doc["holder"]
            else _дней_с(последние.get(doc["id"], doc["created_at"]))
        )
        doc["просрочен"] = _просрочен(doc)
    return rows


def timing(days: int = 90) -> dict:
    """Сколько времени документы проводят на каждом участке.

    Это та самая пустая строка «Время протекания процесса» из карты — только
    посчитанная из движений, а не собранная руками. Медиана, а не среднее: один
    договор, пролежавший месяц в отпуске, сдвинул бы среднее так, что оно
    перестало бы описывать типичный срок.
    """
    порог = (datetime.now() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    with connect() as conn:
        события = [
            dict(r)
            for r in conn.execute(
                "SELECT e.doc_id, e.at, e.action, e.from_holder, e.to_holder, d.kind "
                "FROM doc_flow_event e JOIN doc_flow d ON d.id = e.doc_id "
                "WHERE e.at >= ? ORDER BY e.doc_id, e.id",
                (порог,),
            )
        ]
        # Время документа целиком — тоже ИЗ СОБЫТИЙ: от первого до последнего.
        #
        # Сначала здесь стояли doc_flow.created_at и closed_at, и это была
        # тихая нестыковка: время у людей считалось по одному источнику, время
        # документа — по другому, и суммы между собой не сходились. Поймано
        # проверкой в браузере, где документ, шедший четверо суток, показал
        # «обычно 0 ч». Столбцы created_at/closed_at остаются, но они — копия
        # для удобства запросов, а не источник правды; журнал событий один.
        закрытые = [
            dict(r)
            for r in conn.execute(
                "SELECT d.kind AS kind, MIN(e.at) AS начало, MAX(e.at) AS конец "
                "FROM doc_flow d JOIN doc_flow_event e ON e.doc_id = d.id "
                "WHERE d.state = ? GROUP BY d.id HAVING MAX(e.at) >= ?",
                (ЗАКРЫТ, порог),
            )
        ]

    # Сколько документ пролежал у каждого — ОДНИМ отрезком на одно непрерывное
    # пребывание, а не по одному на каждое событие.
    #
    # Наивный счёт «от события до следующего» разрезает пребывание там, где
    # человек ничего не передавал: виза — отдельное событие, держатель при нём
    # не меняется. Финдиректор, у которого счёт пролежал 50 часов и был
    # завизирован за 2 часа до передачи, получал два отрезка (48 и 2) и медиану
    # 25 вместо честных 50 — то есть отчёт занижал ровно то место, ради
    # обнаружения которого он и делается. Поймано сверкой цифр в браузере.
    у_кого: dict[str, list[float]] = {}
    начало_отрезка: str | None = None
    текущий: str | None = None
    текущий_док: int | None = None

    def закрыть(конец: str | None) -> None:
        nonlocal начало_отрезка, текущий
        if текущий and начало_отрезка and конец:
            часы = _часов(начало_отрезка, конец)
            # Отрицательный отрезок — перевод часов назад или запись задним
            # числом. В медиану такое пускать нельзя: она перестанет описывать
            # что бы то ни было.
            if часы is not None and часы >= 0:
                у_кого.setdefault(текущий, []).append(часы)
        начало_отрезка, текущий = None, None

    for i, e in enumerate(события):
        if e["doc_id"] != текущий_док:
            # Предыдущий документ кончился, а его последний отрезок открыт:
            # документ всё ещё у человека. В статистику не берём — он растёт
            # каждую секунду и портил бы медиану.
            начало_отрезка, текущий = None, None
            текущий_док = e["doc_id"]
        держатель = e["to_holder"]
        if держатель != текущий:
            закрыть(e["at"])
            if держатель:
                начало_отрезка, текущий = e["at"], держатель
        # Событие, не меняющее держателя (виза, отклонение), отрезок не рвёт.
        последнее_в_документе = i + 1 >= len(события) or события[i + 1]["doc_id"] != e["doc_id"]
        if последнее_в_документе and not держатель:
            # Документ закрыт: держателя нет, значит пребывание кончилось.
            закрыть(e["at"])

    по_видам: dict[str, list[float]] = {}
    for d in закрытые:
        часы = _часов(d["начало"], d["конец"])
        if часы is not None and часы >= 0:
            по_видам.setdefault(d["kind"], []).append(часы)

    названия = dict(KINDS)
    return {
        "период_дней": days,
        "у_кого": sorted(
            (
                {
                    "кто": кто,
                    "передач": len(часы),
                    "медиана_часов": _медиана(часы),
                    "дольше_всего_часов": max(часы),
                }
                for кто, часы in у_кого.items()
            ),
            key=lambda x: x["медиана_часов"] or 0,
            reverse=True,
        ),
        "по_видам": sorted(
            (
                {
                    "вид": вид,
                    "название": названия.get(вид, вид),
                    "закрыто": len(часы),
                    "медиана_часов": _медиана(часы),
                }
                for вид, часы in по_видам.items()
            ),
            key=lambda x: x["медиана_часов"] or 0,
            reverse=True,
        ),
    }


def _часов(с: str, по: str) -> float | None:
    try:
        return round(
            (datetime.fromisoformat(по) - datetime.fromisoformat(с)).total_seconds() / 3600, 2
        )
    except (TypeError, ValueError):
        return None


def _медиана(значения: list[float]) -> float | None:
    if not значения:
        return None
    v = sorted(значения)
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else round((v[m - 1] + v[m]) / 2, 2)
