"""FastAPI-приложение fire_safety_backend.

App factory собирает все роутеры из views/ и монтирует статику фронтенда.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .infrastructure import languagetool, llm
from .infrastructure.db import init_db
from .infrastructure.queue import queue
from .services import addressees as addressee_service
from .views import (
    addressees,
    downloads,
    health,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    addressee_service.seed_defaults()
    llm.startup()
    languagetool.startup()
    queue.start()
    log.info(
        "Backend started. Ollama: %s, model: %s",
        config.OLLAMA_HOST,
        config.LLM_MODEL,
    )
    yield
    await queue.stop()
    await llm.shutdown()
    await languagetool.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Ассистент ПБ", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(spellcheck.router)
    app.include_router(legal.router)
    app.include_router(letter.router)
    app.include_router(tasks.router)
    app.include_router(downloads.router)
    app.include_router(addressees.router)

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
