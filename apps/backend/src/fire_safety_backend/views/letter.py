"""Роутер: генерация официального письма."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..infrastructure.queue import queue
from ..models import LetterRequest
from ..pipelines import legacy as pipelines

router = APIRouter(prefix="/api", tags=["letter"])


@router.post("/letter")
async def api_letter(req: LetterRequest) -> dict:
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="Пустой набросок")
    task = await queue.submit(
        "letter",
        lambda t: pipelines.run_letter(req.draft, req.addressee_type, task=t),
    )
    return {"task_id": task.id}
