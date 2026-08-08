"""Роутер: главная страница и статические view-страницы фронтенда.

Страницы отдаются только вошедшим. Без входа — редирект на форму, а не 401:
голый 401 в браузере выглядит как поломка сервера, а не как «представьтесь».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config
from . import auth

router = APIRouter(tags=["frontend"])

_ALLOWED_VIEWS = {
    "spellcheck",
    "legal",
    "letter",
    "history",
    "batch",
    "ask",
    "transport",
    "waybill",
    # Справочники вынесены из рабочих экранов: парк и точки заводят раз в
    # полгода, а «выдать машину» — каждое утро, и держать их на одной
    # странице значило заставлять листать редкое ради частого.
    "reference-fleet",
    "reference-people",
    # «Защита данных» отделена от журнала задач: это две несвязанные вещи,
    # и на общей странице состояние шифрования читалось как часть истории.
    "security",
    # Сводка использования: страница была написана, но в список не попала —
    # то есть открыть её было нельзя ничем, кроме прямого ввода адреса.
    "stats",
    # Сверка таблиц: та же история, что со «Что происходит» — страница
    # написана и ручка работает, а имени в списке не было, и открыть её было
    # нечем.
    "compare",
    # Пересчёт счёта и акта: цена × количество, итог, НДС, сумма прописью.
    "arithmetic",
    # Проверка реквизитов в таблице: контрольные суммы ИНН, ОГРН, счёта и
    # остальных — сразу по всему реестру, а не по одной карточке.
    "requisites",
    # Журнал прохождения документов. Третий подряд случай той же ошибки:
    # страница, ручки и тесты готовы, а строки здесь нет — и снаружи это
    # выглядит как «функции не существует», потому что 404 отдаётся раньше
    # любой проверки. Список закрытый намеренно (иначе `{view}.html` читал бы
    # любой файл), но цена закрытости — вот эта забываемая строка.
    "doc-flow",
}
_LOGIN_PAGE = "login"


def _page(name: str) -> str:
    path = config.FRONTEND_DIR / "views" / f"{name}.html"
    if not path.exists():
        raise HTTPException(status_code=404)
    return path.read_text(encoding="utf-8")


@router.get("/login.html", response_class=HTMLResponse)
async def login_page(user: auth.User | None = Depends(auth.optional_user)):
    # Уже вошёл — на форме входа делать нечего.
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_page(_LOGIN_PAGE))


@router.get("/", response_class=HTMLResponse)
async def index(user: auth.User | None = Depends(auth.optional_user)):
    if user is None:
        return RedirectResponse("/login.html", status_code=303)
    return HTMLResponse((config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8"))


@router.get("/{view}.html", response_class=HTMLResponse)
async def view_page(view: str, user: auth.User | None = Depends(auth.optional_user)):
    if view not in _ALLOWED_VIEWS:
        raise HTTPException(status_code=404)
    if user is None:
        return RedirectResponse("/login.html", status_code=303)
    return HTMLResponse(_page(view))
