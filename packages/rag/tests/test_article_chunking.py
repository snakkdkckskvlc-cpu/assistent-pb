"""Тесты постатейной нарезки нормативных актов.

Регрессия на замеренную причину неверных ссылок: chunk_sentences режет по
количеству слов, и в один чанк живого индекса попадало ВОСЕМЬ разных статей
(ст. 309, 310, 328, 330, 333, 395, 401, 421 в фрагменте на 4238 символов).
Модель получала простыню и для неустойки 2% в день сослалась на ст. 395
вместо верной ст. 333.
"""

from __future__ import annotations

from fire_safety_rag.chunking import chunk_by_articles, chunk_sentences

# Разметка трёх типов — ровно та, что встречается в реальном корпусе.
_LAW = """Статья 330. Понятие неустойки
1. Неустойкой признаётся определённая законом или договором денежная сумма.

Статья 333. Уменьшение неустойки
1. Если подлежащая уплате неустойка явно несоразмерна последствиям нарушения
обязательства, суд вправе уменьшить неустойку.

Статья 395. Ответственность за неисполнение денежного обязательства
1. В случаях неправомерного удержания денежных средств подлежат уплате проценты.
"""

_DECREE = """Пункт 17. Руководитель организации обеспечивает исправное состояние систем.

Пункт 27. Руководитель организации обеспечивает возможность эвакуации людей.
"""

_SP = """1.1. Настоящий свод правил устанавливает нормы проектирования.

5.1.2. Извещатели пожарные следует размещать под перекрытием.
"""


def test_law_split_one_article_per_chunk() -> None:
    chunks = chunk_by_articles(_LAW, 500)
    articles = [c["article"] for c in chunks if c["article"]]
    assert articles == ["Статья 330", "Статья 333", "Статья 395"]
    # Ключевое: статьи РАЗДЕЛЕНЫ, а не свалены в один фрагмент.
    for c in chunks:
        assert (
            sum(marker in c["text"] for marker in ("Статья 330", "Статья 333", "Статья 395")) <= 1
        )


def test_article_333_is_whole_and_findable() -> None:
    """Именно эта статья доезжала до модели на 39% при посимвольной обрезке."""
    chunk = next(c for c in chunk_by_articles(_LAW, 500) if c["article"] == "Статья 333")
    assert "явно несоразмерна" in chunk["text"]
    assert "суд вправе уменьшить неустойку" in chunk["text"]


def test_decree_points_recognised() -> None:
    chunks = chunk_by_articles(_DECREE, 500)
    assert [c["article"] for c in chunks] == ["Пункт 17", "Пункт 27"]


def test_sp_numbering_recognised() -> None:
    chunks = chunk_by_articles(_SP, 500)
    assert [c["article"] for c in chunks] == ["1.1", "5.1.2"]


def test_single_level_numbering_is_not_a_boundary() -> None:
    """В преамбуле СП «1. Разработан…», «2. Внесен…» — это НЕ разделы.
    Одноуровневая нумерация границей считаться не должна."""
    preamble = (
        "1. Разработан ФГУ ВНИИПО МЧС России.\n\n"
        "2. Внесен Техническим комитетом по стандартизации ТК 274.\n\n"
        "3. Утвержден и введен в действие Приказом МЧС России.\n"
    )
    chunks = chunk_by_articles(preamble, 500)
    assert all(c["article"] is None for c in chunks)


def test_decimal_number_in_text_is_not_a_boundary() -> None:
    """«3.5 м» в начале строки не должно превращаться в границу раздела."""
    text = "Статья 1. Общие положения\n3.5 м составляет минимальная высота.\n"
    chunks = chunk_by_articles(text, 500)
    assert [c["article"] for c in chunks] == ["Статья 1"]


def test_oversized_article_is_split_but_keeps_its_number() -> None:
    long_article = "Статья 7. Длинная статья\n" + ("слово " * 400)
    chunks = chunk_by_articles(long_article, 100)
    assert len(chunks) > 1
    assert all(c["article"] == "Статья 7" for c in chunks)


def test_text_without_markup_falls_back_to_sentences() -> None:
    """Письма, документы контрагентов и вывод OCR не должны сломаться."""
    plain = "Первое предложение. Второе предложение. Третье предложение."
    chunks = chunk_by_articles(plain, 500)
    assert [c["text"] for c in chunks] == chunk_sentences(plain, 500)
    assert all(c["article"] is None for c in chunks)


def test_preamble_before_first_article_is_kept() -> None:
    text = "СВОД ПРАВИЛ. Общая шапка документа.\n\nСтатья 1. Первая статья\nТекст."
    chunks = chunk_by_articles(text, 500)
    assert any("Общая шапка" in c["text"] for c in chunks)
    assert any(c["article"] == "Статья 1" for c in chunks)


def test_empty_text() -> None:
    assert chunk_by_articles("", 500) == []
    assert chunk_by_articles("   \n  ", 500) == []


# --- Глава/раздел в метаданных ----------------------------------------------


def test_chapter_is_attached_to_following_articles() -> None:
    """«Статья 333» сама по себе не говорит, что это глава 23 «Обеспечение
    исполнения обязательств» — номер главы нужен в метаданных."""
    text = (
        "Глава 22. Исполнение обязательств\n"
        "Статья 309. Общие положения\nТекст.\n\n"
        "Глава 23. Обеспечение исполнения обязательств\n"
        "Статья 330. Понятие неустойки\nТекст.\n\n"
        "Статья 333. Уменьшение неустойки\nТекст.\n"
    )
    by_article = {c["article"]: c["chapter"] for c in chunk_by_articles(text, 500) if c["article"]}
    assert by_article["Статья 309"] == "Глава 22"
    assert by_article["Статья 330"] == "Глава 23"
    # Глава действует до следующего заголовка, а не только на одну статью.
    assert by_article["Статья 333"] == "Глава 23"


def test_roman_numeral_sections_recognised() -> None:
    text = "Раздел III. Общие положения\nСтатья 1. Первая\nТекст.\n"
    chunks = [c for c in chunk_by_articles(text, 500) if c["article"]]
    assert chunks[0]["chapter"] == "Раздел III"


def test_article_before_any_chapter_has_no_chapter() -> None:
    text = "Статья 1. Первая\nТекст.\n\nГлава 2. Вторая глава\nСтатья 5. Пятая\nТекст.\n"
    by_article = {c["article"]: c["chapter"] for c in chunk_by_articles(text, 500) if c["article"]}
    assert by_article["Статья 1"] is None
    assert by_article["Статья 5"] == "Глава 2"


def test_chapter_key_present_even_without_markup() -> None:
    """Потребитель не должен проверять наличие ключа."""
    assert all("chapter" in c for c in chunk_by_articles("Просто текст без разметки.", 500))
