"""FastAPI-приложение fire_safety_backend.

App factory собирает все роутеры из views/ и монтирует статику фронтенда.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .infrastructure import languagetool, llm, secure_files
from .infrastructure.db import init_db
from .infrastructure.queue import queue
from .services import addressees as addressee_service
from .services import history as history_service
from .services import retention
from .views import (
    addressees,
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


async def _record_task_history(task) -> None:
    # SQLite — блокирующий вызов, уводим с event loop воркера очереди.
    await asyncio.to_thread(history_service.record, task)


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

    app.include_router(health.router)
    app.include_router(spellcheck.router)
    app.include_router(legal.router)
    app.include_router(letter.router)
    app.include_router(batch.router)
    app.include_router(tasks.router)
    app.include_router(downloads.router)
    app.include_router(addressees.router)
    app.include_router(feedback.router)
    app.include_router(history.router)
    app.include_router(data.router)

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
