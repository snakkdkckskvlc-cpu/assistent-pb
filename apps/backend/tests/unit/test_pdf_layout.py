"""Разметка структуры PDF: markdown-таблицы, заголовки, списки, колонки.

pdfplumber в тестах не поднимается — страницы подменяются заглушками с той же
формой данных (`chars`, `extract_text_lines`, `extract_words`). Проверяется
логика РЕШЕНИЯ (что считать заголовком, где колонки, как собрать таблицу), а
не качество разбора PDF самой библиотекой.

Пороги ниже взяты из замеров на реальном корпусе, а не подобраны:
    69-ФЗ            основной кегль 11.0, символов крупнее 14pt — НОЛЬ
    СП 1.13130       основной кегль 10.1, крупнее 14pt — 0.44% (обложка)
    положение НЛМК   основной кегль 12.0, жирных 7.2%
"""

from __future__ import annotations

from fire_safety_backend.infrastructure.parsers import pdf_parser


def _char(size: float = 12.0, bold: bool = False, x0: float = 70.0) -> dict:
    return {
        "size": size,
        "fontname": "Arial-Bold" if bold else "Arial",
        "x0": x0,
        "x1": x0 + 5,
    }


def _line(text: str, size: float = 12.0, bold: bool = False, top: float = 0.0) -> dict:
    return {
        "text": text,
        "top": top,
        "chars": [_char(size, bold) for _ in text or " "],
    }


# --- Markdown-таблицы -------------------------------------------------------


def test_table_becomes_markdown() -> None:
    rows = [["Роль", "Определение"], ["Руководитель", "Руководитель подразделения"]]
    out = pdf_parser._table_to_markdown(rows)
    assert out.splitlines()[0] == "| Роль | Определение |"
    assert out.splitlines()[1] == "| --- | --- |"
    assert "| Руководитель | Руководитель подразделения |" in out


def test_table_cells_are_flattened_to_one_line() -> None:
    """Перенос внутри ячейки сломал бы разметку колонок."""
    out = pdf_parser._table_to_markdown([["a", "b"], ["многострочная\nячейка", "x"]])
    assert "многострочная ячейка" in out
    assert len(out.splitlines()) == 3


def test_pipe_inside_cell_is_escaped() -> None:
    out = pdf_parser._table_to_markdown([["a", "b"], ["зна|чение", "x"]])
    assert r"зна\|чение" in out


def test_ragged_rows_are_padded_to_equal_width() -> None:
    """Рваное число колонок сбивает сопоставление значения со столбцом."""
    out = pdf_parser._table_to_markdown([["a", "b", "c"], ["1"]])
    assert out.splitlines()[-1] == "| 1 |  |  |"


def test_fully_empty_table_gives_nothing() -> None:
    assert pdf_parser._table_to_markdown([[None, ""], ["", None]]) == ""
    assert pdf_parser._table_to_markdown([]) == ""


# --- Заголовки --------------------------------------------------------------


def test_larger_font_is_a_heading() -> None:
    assert pdf_parser._heading_level(_line("РАЗДЕЛ 5", size=24.0), body_size=12.0) == 1
    assert pdf_parser._heading_level(_line("Подраздел", size=14.5), body_size=12.0) == 2


def test_bold_short_line_is_a_heading() -> None:
    """«3. ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ», «5. РОЛИ» из положения НЛМК — жирные, того
    же кегля 12pt, что и основной текст."""
    assert pdf_parser._heading_level(_line("5. РОЛИ", bold=True), body_size=12.0) == 2
    assert (
        pdf_parser._heading_level(_line("3. ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ", bold=True), body_size=12.0) == 2
    )


def test_bold_running_text_is_not_a_heading() -> None:
    """В том же документе жирным выделены перечисления терминов ВНУТРИ
    предложения — заголовками они не являются."""
    long_bold = _line(
        "акт; виза; Компания, оформление документа; согласование; транспортное средство.",
        bold=True,
    )
    assert pdf_parser._heading_level(long_bold, body_size=12.0) == 0


def test_bold_line_ending_with_punctuation_is_not_a_heading() -> None:
    assert pdf_parser._heading_level(_line("Важное замечание:", bold=True), body_size=12.0) == 0


def test_plain_body_line_is_not_a_heading() -> None:
    """69-ФЗ: заголовков по шрифту в документе нет вообще — ни жирного, ни
    крупного кегля. Выдумывать их нельзя, «Статья N» ловится текстовым
    регэкспом в чанкере RAG."""
    line = _line("управление в области пожарной безопасности - деятельность органов")
    assert pdf_parser._heading_level(line, body_size=11.0) == 0


def test_no_body_size_disables_size_rule() -> None:
    """Если кегль документа определить не удалось, размерное правило молчит,
    а не считает заголовком каждую строку."""
    assert pdf_parser._heading_level(_line("Текст", size=20.0), body_size=0.0) == 0


# --- Списки -----------------------------------------------------------------


def test_typographic_bullets_become_list_items() -> None:
    for marker in ("• ", "- ", "– ", "— ", "· "):
        out = pdf_parser._line_to_markdown(_line(f"{marker}пункт списка"), body_size=12.0)
        assert out == "- пункт списка", marker


def test_letter_and_number_markers_are_preserved() -> None:
    """Регрессия, пойманная сравнением с прежним парсером: замена «а)» на «- »
    вычистила из ПП-1479 по 70 вхождений «а)», «б)» и «г)». В нормативных
    актах это адресуемые единицы — на них ссылаются («подпункт б) пункта 17»).
    """
    for marker in ("а)", "б)", "в)", "17)", "1)"):
        out = pdf_parser._line_to_markdown(_line(f"{marker} текст пункта"), body_size=12.0)
        assert out.startswith(marker), out


def test_indented_paragraph_is_not_a_list() -> None:
    """Замерено на 69-ФЗ: строки с увеличенным отступом (x0 85 → 112) — это
    красная строка абзаца, а не пункт списка. Превращать их в «- » значит
    придумывать структуру, которой в документе нет."""
    line = _line("зона пожара - территория, на которой существует угроза")
    line["chars"] = [_char(x0=112.0) for _ in range(20)]
    assert pdf_parser._line_to_markdown(line, body_size=11.0).startswith("зона пожара")


def test_dash_inside_sentence_is_not_a_list_marker() -> None:
    line = _line("управление в области пожарной безопасности - деятельность органов")
    assert not pdf_parser._line_to_markdown(line, body_size=11.0).startswith("- ")


# --- Двухколоночная вёрстка -------------------------------------------------


class _FakePage:
    def __init__(self, words: list[dict], width: float = 600.0, height: float = 800.0) -> None:
        self._words = words
        self.width = width
        self.height = height

    def extract_words(self) -> list[dict]:
        return self._words


def _word(x0: float, x1: float, top: float) -> dict:
    return {"x0": x0, "x1": x1, "top": top, "bottom": top + 10}


def test_two_column_page_is_detected() -> None:
    left = [_word(50, 200, 40 * i) for i in range(18)]
    right = [_word(350, 500, 40 * i) for i in range(18)]
    split = pdf_parser._column_split_x(_FakePage(left + right))
    assert split is not None
    assert 200 < split < 350


def test_narrow_gap_is_not_a_column_split() -> None:
    """Разрыв в 30pt — это межколоночный интервал таблицы, а не вёрстка в две
    колонки. Читать колонки таблицы раздельно нельзя: строка развалится."""
    left = [_word(50, 280, 40 * i) for i in range(18)]
    right = [_word(310, 500, 40 * i) for i in range(18)]
    assert pdf_parser._column_split_x(_FakePage(left + right)) is None


def test_word_crossing_the_middle_cancels_column_hypothesis() -> None:
    """Заголовок во всю ширину означает, что страница не двухколоночная."""
    left = [_word(50, 200, 40 * i) for i in range(18)]
    right = [_word(350, 500, 40 * i) for i in range(18)]
    header = [_word(50, 550, 0)]
    assert pdf_parser._column_split_x(_FakePage(left + right + header)) is None


def test_two_short_blocks_side_by_side_are_not_columns() -> None:
    """Колонки идут через всю страницу; два коротких блока рядом (например,
    реквизиты сторон в шапке договора) — нет."""
    left = [_word(50, 200, 10 * i) for i in range(18)]
    right = [_word(350, 500, 10 * i) for i in range(18)]
    tall = [_word(50, 200, 700)]
    assert pdf_parser._column_split_x(_FakePage(left + right + tall)) is None


def test_sparse_page_is_not_analysed_for_columns() -> None:
    assert pdf_parser._column_split_x(_FakePage([_word(50, 100, 0)])) is None
