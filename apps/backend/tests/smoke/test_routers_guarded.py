"""Каждая ручка `/api/` закрыта входом. Проход по ВСЕМ маршрутам приложения.

Зачем этот тест существует. Роутеры закрываются целиком, списком в
`main.py::create_app`, а не `Depends` по ручкам — правило проекта, потому что
ручек больше сорока и забытая означает открытый доступ к договорам компании из
всей внутренней сети. Но забыть можно и сам роутер: строка в списке
`create_app` такая же строка, как любая другая, и новый модуль легко доехать до
`include_router` без неё.

Глазами сорок маршрутов не проверяются, а один незакрытый выглядит ровно как
сорок закрытых — до первого случая. Этот тест — единственное, что ловит
пропуск механически, поэтому в плане CRM он записан как «писать первым, ещё до
первого нового роутера» (docs/03-architecture/crm-target-design.md §6.1).

Проверяется ИМЕННО 401, а не «не 200»: 404 на чужой объект — тоже не 200, но
означает, что запрос до логики дошёл, то есть роутер открыт.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.main import create_app

# Открыты без входа ровно два места, и оба по названной причине:
#   /api/auth   — иначе войти было бы негде;
#   /api/health — диагностика «сервер жив» нужна до входа, и неавторизованному
#                 он отдаёт только общее состояние, без блока безопасности.
# Список закрытый: расширять его — значит открывать ручку наружу, и это должно
# требовать правки теста, а не проходить незаметно.
ОТКРЫТЫЕ = ("/api/auth", "/api/health")

# Подстановка для параметров пути. Значения заведомо несуществующие: тест
# проверяет ОТКАЗ ДО логики, и до обращения к данным дело дойти не должно.
_ЗАГЛУШКИ = {
    "task_id": "нет-такой-задачи",
    "name": "нет-такого-файла.docx",
    "path": "нет-такого-файла.docx",
    "filename": "нет-такого-файла.docx",
}
_ПАРАМЕТР = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _подставить(path: str) -> str:
    return _ПАРАМЕТР.sub(lambda m: _ЗАГЛУШКИ.get(m.group(1), "0"), path)


def _маршруты() -> list[tuple[str, str]]:
    """Все (метод, путь) приложения, кроме открытых и служебных."""
    out: list[tuple[str, str]] = []
    for route in create_app().routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not path.startswith("/api/") or not methods:
            continue
        if path.startswith(ОТКРЫТЫЕ):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, path))
    return sorted(out)


def test_there_are_routes_to_check() -> None:
    """Страховка от «тест зелёный, потому что ничего не проверил».

    Если сборка маршрутов однажды сломается и вернёт пустой список, тест выше
    станет зелёным на пустом множестве — то есть перестанет что-либо значить,
    не подав об этом знака.
    """
    assert len(_маршруты()) >= 20


@pytest.mark.parametrize(("method", "path"), _маршруты())
def test_api_route_requires_login(anon_client: TestClient, method: str, path: str) -> None:
    r = anon_client.request(method, _подставить(path))
    assert r.status_code == 401, (
        f"{method} {path} отвечает {r.status_code} без входа — "
        f"роутер не попал в защищённый список в main.py::create_app"
    )


def test_health_and_login_stay_open(anon_client: TestClient) -> None:
    """Обратная сторона: закрыть эти две — значит запереть вход снаружи."""
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/auth/me").status_code == 200
