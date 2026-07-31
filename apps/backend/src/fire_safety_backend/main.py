"""FastAPI-приложение fire_safety_backend.

App factory собирает все роутеры из views/ и монтирует статику фронтенда.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .infrastructure import languagetool, llm, netguard, secure_files, task_store
from .infrastructure.db import init_db
from .infrastructure.queue import queue
from .services import addressees as addressee_service
from .services import history as history_service
from .services import retention
from .views import (
    addressees,
    auth,
    batch,
    data,
    downloads,
    feedback,
    health,
    history,
    legal,
    letter,
    spellcheck,
    static_pages,
    tasks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Запрет выхода в интернет включается НА ИМПОРТЕ этого модуля, а не в
# lifespan. Две причины. Первая: офлайн-флаги huggingface_hub читаются им при
# импорте, а RAG подгружается лениво уже после старта — выставить их надо
# заранее. Вторая: это единственная точка, через которую проходят оба способа
# запуска — и десктопное окно, и `uvicorn fire_safety_backend.main:app`.
#
# Скрипты (scripts/index_corpus.py, index_letters.py, warm_models.py) этот
# модуль НЕ импортируют, поэтому им сеть остаётся: первая загрузка модели
# эмбеддингов идёт из интернета.
netguard.install()


async def _record_task_history(task) -> None:
    # SQLite — блокирующий вызов, уводим с event loop воркера очереди.
    await asyncio.to_thread(history_service.record, task)
    # Плюс полный результат — чтобы перезапуск сервера не терял то, что
    # человек ждал минутами и ещё не успел скачать. Текст договора внутри,
    # поэтому в базу он ложится зашифрованным (infrastructure/task_store.py).
    await asyncio.to_thread(task_store.save, task)


async def _retention_loop() -> None:
    """Периодическая очистка рабочих файлов, пока приложение открыто.

    Первый проход — сразу, но уже в фоне: старт backend'а специально доводили
    до 3.2 c, и ставить перед ним обход каталогов незачем. Дальше — раз в
    DATA_RETENTION_SWEEP_SEC, чтобы долгую сессию тоже накрывало.
    """
    while True:
        try:
            await asyncio.to_thread(retention.purge_expired)
        except Exception:
            # Очистка не должна валить приложение: не удалить старый файл —
            # неприятно, но не смертельно.
            log.exception("Автоочистка рабочих файлов не удалась")
        await asyncio.sleep(config.DATA_RETENTION_SWEEP_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    addressee_service.seed_defaults()
    # Возобновить прерванные задачи нельзя — работа модели не сохраняется.
    # Но и оставить их «в очереди» навсегда нельзя: человек ждал бы ответа,
    # которого не будет.
    interrupted = task_store.mark_interrupted()
    if interrupted:
        log.warning("Прервано перезапуском задач: %d", interrupted)
    llm.startup()
    languagetool.startup()
    queue.on_task_finished = _record_task_history
    queue.start()
    security = secure_files.status()
    retention_task = asyncio.create_task(_retention_loop())
    log.info(
        "Backend started. Ollama: %s, model: %s, шифрование: %s (%s), хранение: %s",
        config.OLLAMA_HOST,
        config.LLM_MODEL,
        security.mode,
        security.reason,
        f"{config.DATA_RETENTION_DAYS} дн." if config.DATA_RETENTION_DAYS > 0 else "бессрочно",
    )
    yield
    retention_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await retention_task
    await queue.stop()
    await llm.shutdown()
    await languagetool.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Ассистент ПБ", lifespan=lifespan)

    # Открыты без входа ровно два роутера:
    #   auth   — иначе войти было бы негде;
    #   health — диагностика «сервер жив, Ollama на месте» нужна до входа, и
    #            сам он отдаёт неавторизованному только общее состояние
    #            (см. views/health.py), без блока безопасности.
    app.include_router(auth.router)
    app.include_router(health.router)

    # Всё остальное — только после входа. Зависимость навешивается на роутер
    # ЦЕЛИКОМ, а не на каждую ручку: ручек больше двадцати, они добавляются, и
    # забытый Depends на новой означал бы открытый доступ к договорам компании
    # из всей внутренней сети.
    guarded = [Depends(auth.current_user)]
    for module in (
        spellcheck,
        legal,
        letter,
        batch,
        tasks,
        downloads,
        addressees,
        feedback,
        history,
        data,
    ):
        app.include_router(module.router, dependencies=guarded)

    # Frontend (статика + HTML-страницы) — только если каталог существует
    if config.FRONTEND_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(config.FRONTEND_DIR)),
            name="static",
        )
        app.include_router(static_pages.router)

    return app


app = create_app()
