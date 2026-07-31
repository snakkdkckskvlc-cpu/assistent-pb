"""Тесты заселения готового индекса (prebuilt_chroma → база пользователя).

Заселение выполняется на КАЖДОЙ установке и обновлении, поэтому опасно
двумя способами: может затереть уже накопленные данные пользователя и может
задеть приватные коллекции (архив писем компании лежит в той же базе).
Оба отказа тихие — поиск просто начнёт отвечать иначе.

Эмбеддинг-функция подменяется фейком: настоящая тянет модель на 1.3 ГБ, а
заселение эмбеддинги не считает — оно копирует уже готовые векторы, так что
для проверки логики настоящая модель не нужна.
"""

from __future__ import annotations

import gc
import hashlib
import shutil
from pathlib import Path

import pytest

chromadb = pytest.importorskip("chromadb")

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings  # noqa: E402
from fire_safety_rag import config, seed  # noqa: E402

_DIM = 3


class _FakeEmbeddingFunction(EmbeddingFunction):
    """Ничего не считает — при заселении векторы приходят готовыми."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002
        return [[0.0] * _DIM for _ in input]

    @staticmethod
    def name() -> str:
        return "fake_seed_ef"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> _FakeEmbeddingFunction:
        return _FakeEmbeddingFunction()


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from chromadb.utils import embedding_functions

    monkeypatch.setattr(
        embedding_functions,
        "SentenceTransformerEmbeddingFunction",
        lambda *a, **kw: _FakeEmbeddingFunction(),
    )


def _make_prebuilt(path: Path, count: int = 3) -> Path:
    """Готовый индекс, какой лежит в git."""
    client = chromadb.PersistentClient(path=str(path))
    col = client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        embedding_function=_FakeEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    col.add(
        ids=[f"pre_{i}" for i in range(count)],
        embeddings=[[float(i), 0.0, 0.0] for i in range(count)],
        documents=[f"норма {i}" for i in range(count)],
        metadatas=[{"source": f"law_{i}.txt", "status": "actual"} for i in range(count)],
    )
    return path


def _collection(path: Path, name: str):
    client = chromadb.PersistentClient(path=str(path))
    return client.get_collection(name=name, embedding_function=_FakeEmbeddingFunction())


def test_seeds_into_empty_database(tmp_path: Path) -> None:
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"

    assert seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt) is True
    assert _collection(target, config.COLLECTION_NAME).count() == 3


def test_seeding_carries_documents_and_metadata(tmp_path: Path) -> None:
    """Переносить одни id бессмысленно — поиску нужны текст и метаданные."""
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"
    seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt)

    got = _collection(target, config.COLLECTION_NAME).get(include=["documents", "metadatas"])
    assert sorted(got["documents"]) == ["норма 0", "норма 1", "норма 2"]
    assert {m["source"] for m in got["metadatas"]} == {"law_0.txt", "law_1.txt", "law_2.txt"}
    assert all(m["status"] == "actual" for m in got["metadatas"])


def test_does_not_overwrite_existing_data(tmp_path: Path) -> None:
    """У пользователя уже проиндексировано своё — заселение обязано отступить."""
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"

    client = chromadb.PersistentClient(path=str(target))
    own = client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        embedding_function=_FakeEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    own.add(
        ids=["мой_документ"],
        embeddings=[[9.0, 9.0, 9.0]],
        documents=["мой собственный документ"],
        metadatas=[{"source": "моё.txt"}],
    )

    assert seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt) is False
    got = _collection(target, config.COLLECTION_NAME).get(include=["documents"])
    assert got["documents"] == ["мой собственный документ"]


def test_does_not_touch_private_letters_collection(tmp_path: Path) -> None:
    """Архив писем компании живёт в той же базе и трогать его нельзя."""
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"

    client = chromadb.PersistentClient(path=str(target))
    letters = client.get_or_create_collection(
        name=config.LETTERS_COLLECTION_NAME,
        embedding_function=_FakeEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    letters.add(
        ids=["письмо_1"],
        embeddings=[[5.0, 5.0, 5.0]],
        documents=["коммерческое письмо компании"],
        metadatas=[{"source": "письмо.docx"}],
    )

    assert seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt) is True

    kept = _collection(target, config.LETTERS_COLLECTION_NAME).get(include=["documents"])
    assert kept["documents"] == ["коммерческое письмо компании"]


def test_missing_prebuilt_is_not_an_error(tmp_path: Path) -> None:
    """Нет готового индекса — просто индексируем с нуля, а не падаем."""
    assert (
        seed.ensure_seeded(chroma_dir=tmp_path / "chroma", prebuilt_dir=tmp_path / "нет") is False
    )


def test_seeding_twice_is_idempotent(tmp_path: Path) -> None:
    """Второй запуск (обновление) не должен дублировать чанки."""
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"

    assert seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt) is True
    assert seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt) is False
    assert _collection(target, config.COLLECTION_NAME).count() == 3


def _snapshot(directory: Path) -> dict[str, str]:
    """SHA-256 всех файлов каталога — чтобы поймать ЛЮБУЮ запись в эталон."""
    gc.collect()  # отпускаем клиентов ChromaDB, иначе замер поймает их дозапись
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.mark.skipif(not seed.PREBUILT_DIR.exists(), reason="нет prebuilt_chroma")
def test_seeding_does_not_modify_prebuilt(tmp_path: Path) -> None:
    """Эталонный индекс лежит в git и обязан остаться байт в байт прежним.

    ChromaDB меняет chroma.sqlite3 уже на `PersistentClient(path)` — ДО любого
    чтения (замерено на chromadb 1.5.9: 7 файлов эталона, изменяется один).
    Пока эталон читался напрямую, `git status` после первой же установки
    переставал быть чистым, а обновлялка (`updater._is_safe_to_update`) на
    грязной рабочей копии отказывается работать. То есть установка молча и
    навсегда отключала автообновление: новые версии просто переставали
    приходить, без единого сообщения.

    Тест идёт на КОПИИ настоящего индекса из репозитория, а не на
    синтетическом, и это не лишняя тяжесть. База, созданная тут же текущей
    версией chromadb, при повторном открытии не меняется — на ней тест
    зеленел бы и со старым, дырявым кодом, то есть не умел бы краснеть.
    Проверено: подменяешь копию на чтение напрямую — краснеет только этот
    вариант.
    """
    prebuilt = tmp_path / "prebuilt"
    shutil.copytree(seed.PREBUILT_DIR, prebuilt)

    before = _snapshot(prebuilt)
    seed.ensure_seeded(chroma_dir=tmp_path / "chroma", prebuilt_dir=prebuilt)
    after = _snapshot(prebuilt)

    assert after == before, "заселение изменило эталонный prebuilt_chroma"


def test_seeding_leaves_no_temporary_copy_behind(tmp_path: Path) -> None:
    """Копия эталона — 37 МБ; забытая, она копилась бы с каждой установки."""
    prebuilt = _make_prebuilt(tmp_path / "prebuilt")
    target = tmp_path / "chroma"

    seed.ensure_seeded(chroma_dir=target, prebuilt_dir=prebuilt)

    leftovers = list((tmp_path / "tmp").glob("prebuilt_*"))
    assert leftovers == [], f"осталась временная копия: {leftovers}"


def test_corrupt_prebuilt_does_not_break_install(tmp_path: Path) -> None:
    """Битый готовый индекс не должен ронять установку — просто вернём False."""
    broken = tmp_path / "prebuilt"
    broken.mkdir()
    (broken / "chroma.sqlite3").write_text("это не база данных", encoding="utf-8")

    assert seed.ensure_seeded(chroma_dir=tmp_path / "chroma", prebuilt_dir=broken) is False
