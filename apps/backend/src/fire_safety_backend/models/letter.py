"""Pydantic-модели запросов, связанных с письмом."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LetterRequest(BaseModel):
    draft: str = Field(min_length=1)
    # Свободная строка — берётся из справочника адресатов
    # (apps/backend/.../services/addressees.py). Пользователь может добавлять
    # новые типы прямо из UI, значения сохраняются в data/app.db.
    addressee_type: str = Field(default="заказчик", max_length=100)


class LetterFields(BaseModel):
    """Поля письма для сборки DOCX (views/letter.py::api_letter_render).

    Ровно то, что подставляется в фирменный бланк (см.
    infrastructure/generators/letter_docx.py) — приходит либо как есть из
    ответа LLM (run_letter), либо уже отредактированным пользователем в
    интерфейсе. Все поля опциональны — пустое значение просто оставит
    соответствующее место в бланке пустым, а не уронит запрос."""

    тема: str = ""
    получатель: str = ""
    обращение: str = ""
    тело: str = ""
    должность_отправителя_placeholder: str = "Директор"
    фио_отправителя_placeholder: str = "О.Н. Сляднев"
