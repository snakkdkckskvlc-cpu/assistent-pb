"""Вопрос по документу: разметка мест и проверка цитат.

Обе вещи здесь — про доверие к ответу. Ответ по документу ценен ровно тем, что
его можно проверить: пойти по ссылке и увидеть то же самое своими глазами. Если
номер места выдуман или цитаты в документе нет, ответ выглядит убедительнее
настоящего, и это хуже, чем «не найдено».
"""

from __future__ import annotations

from pathlib import Path

from fire_safety_backend.pipelines.ask import (
    _merge_answers,
    answer_text,
    build_blocks,
    rescue_misplaced_sources,
    verify_sources,
)

# --- Разметка мест ---


def test_pasted_text_is_labelled_by_fragments_not_pages() -> None:
    """У вставленного текста страниц не существует. Написать «стр. 3» значило бы
    придумать номер, по которому человек пойдёт искать место в файле."""
    blocks, warning = build_blocks("Первый абзац.\n\nВторой абзац.", source_path=None)
    assert all(label.startswith("фрагмент") for label, _ in blocks)
    assert "фрагмент" in warning


def test_docx_is_labelled_by_fragments_and_says_why() -> None:
    """У DOCX разбиение на страницы появляется только при печати и зависит от
    шрифта и полей. Предупреждение обязано это объяснить."""
    blocks, warning = build_blocks("Текст договора.", source_path=Path("data/uploads/дог.docx"))
    assert all(label.startswith("фрагмент") for label, _ in blocks)
    assert ".docx" in warning


def test_long_text_is_split_into_several_blocks() -> None:
    text = "\n\n".join(f"Абзац номер {i}. " + "слово " * 120 for i in range(6))
    blocks, _ = build_blocks(text, source_path=None)
    assert len(blocks) > 1
    assert [label for label, _ in blocks] == [f"фрагмент {i}" for i in range(1, len(blocks) + 1)]


def test_paragraph_is_not_cut_in_the_middle() -> None:
    """Разрыв абзаца посередине рвёт и цитату: половина фразы попадёт в один
    кусок, половина в другой, и проверить её будет нечем."""
    text = "\n\n".join(f"Абзац {i} со своим содержимым." for i in range(30))
    blocks, _ = build_blocks(text, source_path=None)
    for _, body in blocks:
        assert not body.startswith("со своим")


# --- Проверка цитат ---


_BLOCKS = [
    ("стр. 1", "Подрядчик обязуется выполнить монтаж автоматической пожарной сигнализации."),
    ("стр. 2", "Ответственный исполнитель — Иванов Иван Иванович, главный инженер."),
]


def test_quote_found_in_the_named_place_is_verified() -> None:
    sources = [{"место": "стр. 2", "цитата": "Иванов Иван Иванович, главный инженер"}]
    assert verify_sources(sources, _BLOCKS)[0]["проверено"] is True


def test_invented_quote_is_marked_unverified() -> None:
    """Главный случай. Выдуманная цитата с правдоподобным номером страницы —
    это ответ, которому нельзя верить и который выглядит достовернее всех."""
    sources = [{"место": "стр. 1", "цитата": "Заказчик выплачивает премию в размере 30 процентов"}]
    checked = verify_sources(sources, _BLOCKS)
    assert checked[0]["проверено"] is False
    assert "не найдена" in checked[0]["почему"]


def test_real_quote_with_wrong_place_gets_the_place_corrected() -> None:
    """Модель устойчиво пишет «стр. 1» там, где место называется «фрагмент 1»
    (наблюдалось на живом прогоне). Цитата при этом настоящая: выбросить верную
    ссылку из-за неверной подписи — потеря на ровном месте. Если цитата
    встречается ровно в одном куске, место исправляется."""
    sources = [{"место": "стр. 1", "цитата": "Иванов Иван Иванович, главный инженер"}]
    checked = verify_sources(sources, _BLOCKS)
    assert checked[0]["проверено"] is True
    assert checked[0]["место"] == "стр. 2"
    assert "уточнено" in checked[0]["почему"]


def test_quote_present_in_several_places_is_not_auto_corrected() -> None:
    """Исправлять место можно, только когда оно однозначно."""
    blocks = [
        ("фрагмент 1", "Работы выполняются в срок."),
        ("фрагмент 2", "Работы выполняются в срок."),
    ]
    checked = verify_sources([{"место": "стр. 9", "цитата": "Работы выполняются в срок"}], blocks)
    assert checked[0]["проверено"] is False
    assert "нескольких" in checked[0]["почему"]


def test_too_short_quote_is_not_accepted() -> None:
    """«монтаж» найдётся в любом договоре и не подтверждает ничего."""
    checked = verify_sources([{"место": "стр. 1", "цитата": "монтаж"}], _BLOCKS)
    assert checked[0]["проверено"] is False


def test_verification_ignores_whitespace_and_case() -> None:
    sources = [{"место": "стр. 1", "цитата": "выполнить   МОНТАЖ автоматической пожарной"}]
    assert verify_sources(sources, _BLOCKS)[0]["проверено"] is True


# --- Склейка ответов по кускам ---


def test_blocks_without_findings_are_dropped() -> None:
    """«В этом фрагменте не найдено», повторённое десять раз, — это шум, а не
    ответ."""
    merged = _merge_answers(
        [
            {"ответ": "Ничего не найдено.", "найдено": False, "источники": []},
            {"ответ": "Исполнитель — Иванов.", "найдено": True, "источники": [{"место": "стр. 2"}]},
            {"ответ": "", "найдено": False, "источники": []},
        ]
    )
    assert merged["ответ"] == "Исполнитель — Иванов."
    assert merged["найдено"] is True
    assert len(merged["источники"]) == 1


def test_nothing_found_anywhere_is_reported_as_not_found() -> None:
    merged = _merge_answers([{"ответ": "Нет данных.", "найдено": False, "источники": []}])
    assert merged["найдено"] is False
    assert merged["источники"] == []


# --- Ответ модели бывает не строкой ---


def test_answer_given_as_a_list_is_rendered_as_text() -> None:
    """Модель охотно возвращает «ответ» списком, особенно когда уточнение
    просит форму ответа списком. Наблюдалось на живом прогоне: пользователю
    показывалось ['Начало работ — ...'] вместе со скобками и кавычками, то есть
    питоновский синтаксис вместо ответа."""
    got = answer_text(["Начало работ — 3 дня.", "Окончание — 45 дней."])
    assert got == "— Начало работ — 3 дня.\n— Окончание — 45 дней."


def test_answer_given_as_a_string_is_left_alone() -> None:
    assert answer_text("  Исполнитель — Иванов.  ") == "Исполнитель — Иванов."


def test_empty_answer_with_quotes_is_not_thrown_away() -> None:
    """Цитаты и есть самое ценное. Кусок, где модель дала источники, но не
    собрала связный текст, выбрасывать нельзя — иначе пользователь увидит
    «не найдено» при найденном."""
    merged = _merge_answers(
        [{"ответ": [], "найдено": True, "источники": [{"место": "стр. 1", "цитата": "текст"}]}]
    )
    assert merged["найдено"] is True
    assert merged["источники"]
    assert merged["ответ"]
    assert "[]" not in merged["ответ"]


# --- Модель кладёт источники не в то поле ---


def test_sources_put_into_the_answer_field_are_moved_back() -> None:
    """Третья форма ответа помимо строки и списка строк: модель кладёт в
    «ответ» список ИСТОЧНИКОВ, а «источники» оставляет пустым. Наблюдалось на
    замере: на вопрос «сколько человек в бригаде» пользователь получил бы текст
    «цитата: … Ковалёва И. П. …» при пустой таблице ссылок — правдоподобный
    ответ не на тот вопрос и без единого подтверждения."""
    result = rescue_misplaced_sources(
        {
            "ответ": [
                {"место": "стр. 1", "цитата": "в лице Ковалёва И. П.", "что_подтверждает": "ФИО"},
                "Обычная строка ответа.",
            ],
            "источники": [],
            "найдено": True,
        }
    )
    assert len(result["источники"]) == 1
    assert result["источники"][0]["цитата"] == "в лице Ковалёва И. П."
    assert result["ответ"] == ["Обычная строка ответа."]


def test_normal_answer_is_untouched() -> None:
    original = {"ответ": ["Начало работ — 3 дня."], "источники": [{"место": "стр. 1"}]}
    assert rescue_misplaced_sources(original) == original


def test_string_answer_is_untouched() -> None:
    original = {"ответ": "Исполнитель — Иванов.", "источники": []}
    assert rescue_misplaced_sources(original) == original
