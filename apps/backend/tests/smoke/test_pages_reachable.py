"""Каждая написанная страница открывается. Проход по файлам `views/`, а не по списку.

Зачем этот тест существует. Страницы отдаёт `views/static_pages.py` по закрытому
списку имён: `/{view}.html` без списка читал бы из каталога любой файл, поэтому
список нужен. Но цена закрытости — строка, которую забывают, и забывали её ТРИЖДЫ
подряд: «Сводка использования», «Сверка таблиц», «Журнал прохождения документов».
Каждый раз страница, ручки и тесты были готовы, а снаружи функции не
существовало: 404 отдаётся раньше любой проверки, и выглядит это не как
недоделка, а как «такого экрана нет».

Ни один из существовавших тестов этого не ловил, потому что все они шли ОТ
списка: брали имя из `_ALLOWED_VIEWS` и проверяли, что оно открывается.
Пропущенное имя в такой проверке не участвует вовсе — тест зелёный ровно
потому, что страницу забыли.

Поэтому здесь наоборот: источник истины — файлы на диске. Появился
`views/что-то.html` — он обязан открываться.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend import config
from fire_safety_backend.views.static_pages import _ALLOWED_VIEWS

# Единственная страница, которую намеренно нет в списке: у формы входа свой
# маршрут, и он делает обратное остальным — вошедшего с неё уводит.
НЕ_ИЗ_СПИСКА = {"login"}


def _страницы() -> list[str]:
    return sorted(p.stem for p in (config.FRONTEND_DIR / "views").glob("*.html"))


def test_there_are_pages_to_check() -> None:
    """Страховка от «зелено, потому что ничего не проверено»: если каталог
    однажды переедет, `glob` вернёт пусто и проверка ниже станет пустой."""
    assert len(_страницы()) >= 10


@pytest.mark.parametrize("имя", _страницы())
def test_every_written_page_is_reachable(client: TestClient, имя: str) -> None:
    if имя in НЕ_ИЗ_СПИСКА:
        pytest.skip("своя ручка")
    assert имя in _ALLOWED_VIEWS, (
        f"views/{имя}.html написана, но её имени нет в _ALLOWED_VIEWS — "
        f"снаружи страница отдаёт 404, как будто функции не существует"
    )
    assert client.get(f"/{имя}.html").status_code == 200


def test_unknown_page_is_not_served(client: TestClient) -> None:
    """Обратная сторона: список закрыт не для красоты. Без него `{view}.html`
    читал бы из каталога всё подряд по имени из адресной строки."""
    assert client.get("/нет-такой-страницы.html").status_code == 404
    assert client.get("/login.html").status_code in (200, 303)


def test_list_has_no_names_without_files(client: TestClient) -> None:
    """Имя в списке без файла — 404 с другой стороны: пункт меню есть, а
    страницы нет. Так бывает после переименования файла."""
    лишние = sorted(_ALLOWED_VIEWS - set(_страницы()))
    assert not лишние, f"в _ALLOWED_VIEWS есть имена без файлов: {лишние}"
