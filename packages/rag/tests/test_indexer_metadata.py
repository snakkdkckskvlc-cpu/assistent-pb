"""Инварианты метаданных, от которых зависит видимость документа в поиске.

Ретривер отсекает отменённые редакции фильтром {"status": {"$ne":
"superseded"}}. Поведение такого фильтра на документах БЕЗ поля status
зависит от версии ChromaDB, поэтому индексатор проставляет status всегда —
даже документам, которых нет в _meta.json. Если этот инвариант когда-нибудь
нарушат, отказ будет тихим: документы останутся в базе, но перестанут
находиться после очередного обновления библиотеки.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

chromadb = pytest.importorskip("chromadb")

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings  # noqa: E402
from fire_safety_rag import config  # noqa: E402
from fire_safety_rag.indexer import build_index  # noqa: E402

_DIM = 3


class _FakeEmbeddingFunction(EmbeddingFunction):
    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002
        return [[0.0] * _DIM for _ in input]

    @staticmethod
    def name() -> str:
        return "fake_indexer_ef"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> _FakeEmbeddingFunction:
        return _FakeEmbeddingFunction()


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from chromadb.utils import embedding_functions

    monkeypatch.setattr(
        embedding_functions,
        "SentenceTransformerEmbeddingFunction",
        lambda *a, **kw: _FakeEmbeddingFunction(),
    )


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    d = tmp_path / "corpus"
    d.mkdir()
    return d


def _metadatas(chroma_dir: Path) -> list[dict]:
    client = chromadb.PersistentClient(path=str(chroma_dir))
    col = client.get_collection(
        name=config.COLLECTION_NAME, embedding_function=_FakeEmbeddingFunction()
    )
    return col.get(include=["metadatas"])["metadatas"]


def test_document_without_meta_entry_still_gets_status(corpus: Path) -> None:
    """Документ, которого нет в _meta.json, обязан остаться видимым для поиска."""
    (corpus / "случайный_документ.txt").write_text(
        "Статья 1. Некоторая норма про пожарную безопасность объектов защиты.",
        encoding="utf-8",
    )
    build_index(corpus_dir=corpus, reset=True)

    metas = _metadatas(config.CHROMA_DIR)
    assert metas, "документ не проиндексировался"
    assert all(m.get("status") for m in metas), "есть чанки без status — исчезнут из выдачи"


def test_meta_entry_status_wins_over_default(corpus: Path) -> None:
    """Явный superseded из _meta.json не должен затираться дефолтным actual."""
    (corpus / "старая_редакция.txt").write_text(
        "Статья 1. Утратившая силу норма.", encoding="utf-8"
    )
    (corpus / "_meta.json").write_text(
        json.dumps(
            {"старая_редакция.txt": {"doc_type": "sp", "status": "superseded"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_index(corpus_dir=corpus, reset=True)

    metas = _metadatas(config.CHROMA_DIR)
    assert metas
    assert all(m["status"] == "superseded" for m in metas)


def test_sidecar_metadata_reaches_chunks(corpus: Path) -> None:
    """act_number/doc_type нужны потребителю, чтобы проверить ссылку модели."""
    (corpus / "закон.txt").write_text(
        "Статья 5. Требования к системам противопожарной защиты.", encoding="utf-8"
    )
    (corpus / "_meta.json").write_text(
        json.dumps(
            {
                "закон.txt": {
                    "doc_type": "federal_law",
                    "act_number": "123-ФЗ",
                    "status": "actual",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_index(corpus_dir=corpus, reset=True)

    metas = _metadatas(config.CHROMA_DIR)
    assert metas
    assert all(m["act_number"] == "123-ФЗ" for m in metas)
    assert all(m["doc_type"] == "federal_law" for m in metas)


def test_meta_json_itself_is_not_indexed(corpus: Path) -> None:
    """_meta.json — служебный файл, в выдаче ему делать нечего."""
    (corpus / "документ.txt").write_text("Статья 1. Норма.", encoding="utf-8")
    (corpus / "_meta.json").write_text("{}", encoding="utf-8")
    build_index(corpus_dir=corpus, reset=True)

    sources = {m["source"] for m in _metadatas(config.CHROMA_DIR)}
    assert "_meta.json" not in sources


def test_reindex_does_not_duplicate_unchanged_files(corpus: Path) -> None:
    """Автообновление гоняет индексацию на каждое обновление приложения."""
    (corpus / "документ.txt").write_text("Статья 1. Норма про эвакуацию.", encoding="utf-8")
    first = build_index(corpus_dir=corpus, reset=True)
    second = build_index(corpus_dir=corpus)

    assert second["files_indexed"] == 0
    assert second["skipped"] == 1
    assert len(_metadatas(config.CHROMA_DIR)) == first["chunks_added"]
