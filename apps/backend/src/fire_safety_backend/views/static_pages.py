"""Роутер: главная страница и статические view-страницы фронтенда."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from .. import config

router = APIRouter(tags=["frontend"])

_ALLOWED_VIEWS = {"spellcheck", "legal", "letter"}


@router.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@router.get("/{view}.html", response_class=HTMLResponse)
async def view_page(view: str) -> str:
    if view not in _ALLOWED_VIEWS:
        raise HTTPException(status_code=404)
    path = config.FRONTEND_DIR / "views" / f"{view}.html"
    if not path.exists():
        raise HTTPException(status_code=404)
    return path.read_text(encoding="utf-8")
