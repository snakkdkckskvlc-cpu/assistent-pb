"""Пересчёт человеческих единиц в целые, в которых хранит БД.

Наружу приложение говорит километрами, литрами и рублями — так вводит человек
и так напечатано в бланке. В БД всё лежит целым: метры, миллилитры, копейки,
сотые доли нормы. Топливо и километраж складываются сотнями рейсов, и
накопленная погрешность float'а превратилась бы в расхождение с бухгалтерией,
которое нечем объяснить.

Округление до целого происходит ровно здесь, на входе. Дальше вся арифметика
идёт в целых.
"""

from __future__ import annotations


def km_to_m(km: float | None) -> int | None:
    return None if km is None else round(km * 1000)


def m_to_km(m: int | None) -> float | None:
    return None if m is None else round(m / 1000, 3)


def l_to_ml(litres: float | None) -> int | None:
    return None if litres is None else round(litres * 1000)


def ml_to_l(ml: int | None) -> float | None:
    return None if ml is None else round(ml / 1000, 3)


def x100_to_int(value: float | None) -> int | None:
    """Дробное в сотые доли: норма 7.8 л/100 км → 780, коэффициент 1.15 → 115."""
    return None if value is None else round(value * 100)


def x100_to_float(hundredths: int | None) -> float | None:
    return None if hundredths is None else round(hundredths / 100, 2)


def rub_to_kop(rub: float | None) -> int | None:
    return None if rub is None else round(rub * 100)


def kop_to_rub(kop: int | None) -> float | None:
    return None if kop is None else round(kop / 100, 2)


def bool_to_int(value: bool | None) -> int | None:
    return None if value is None else int(value)
