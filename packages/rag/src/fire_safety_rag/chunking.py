"""Sentence-aware чанкинг текста.

Раньше и здесь (```_chunk_text``), и в backend (`pipelines/legacy.py::
_chunk_by_words`) текст резался по количеству слов без учёта границ
предложений — один и тот же код, продублированный в двух пакетах.
Теперь оба используют эту функцию.

Алгоритм (по мотивам sentence-aware чанкера private-gpt, см.
references/private-gpt-main/README_reference.md): сначала делим на
предложения (NLTK Punkt, модель для русского — vendored offline в
resources/nltk_data, сеть не нужна), затем жадно упаковываем предложения
в чанки по ограничению в словах. Предложение, которое само по себе длиннее
лимита, режем по словам принудительно — это единственный случай, где
чанк может начаться не с начала предложения.

Качество на русских датах вида «12.01.2026г.» неидеально: Punkt распознаёт
отдельное «г» как сокращение (resources/nltk_data/.../abbrev_types.txt),
но не «2026г» одним токеном, поэтому такая дата иногда становится границей
предложения. Всё равно строго лучше, чем резать по словам без разбора.
"""

from __future__ import annotations

import re
from pathlib import Path

import nltk

_NLTK_DATA_DIR = Path(__file__).resolve().parent / "resources" / "nltk_data"
if str(_NLTK_DATA_DIR) not in nltk.data.path:
    nltk.data.path.insert(0, str(_NLTK_DATA_DIR))


# Границы структурных единиц нормативных актов. Разметка отличается по типу
# документа — проверено на реальном корпусе:
#   ГК, ФЗ, КоАП        → «Статья 333. Понятие неустойки»
#   ПП РФ №1479         → «Пункт 17. Руководитель организации обеспечивает…»
#   СП (своды правил)   → «1.1.», «5.1.2.» с начала строки
# У СП требуется МИНИМУМ два уровня нумерации: одноуровневое «1.» в этих
# документах — это преамбула («1. Разработан ФГУ ВНИИПО…», «2. Внесен ТК 274»),
# а не раздел, и ловить её как границу нельзя. После номера обязателен пробел и
# буква — иначе размер «3.5 м» в начале строки стал бы ложной границей.
_ARTICLE_BOUNDARY = re.compile(
    r"^[ \t]*("
    r"Статья\s+\d+(?:\.\d+)*"
    r"|Пункт\s+\d+(?:\.\d+)*"
    # После номера раздела обязательна ЗАГЛАВНАЯ буква (или кавычка/скобка):
    # раздел нормативного акта всегда начинается с прописной, а «3.5 м
    # составляет…» — это размер в тексте, и строчная «м» его отсекает.
    r"|\d+\.\d+(?:\.\d+)*\.?(?=\s+[А-ЯЁA-Z«\"(])"
    r")",
    re.MULTILINE,
)


def _split_sentences(text: str) -> list[str]:
    return [s for s in nltk.tokenize.sent_tokenize(text, language="russian") if s.strip()]


def _split_long_sentence(sentence: str, max_words: int) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def chunk_by_articles(text: str, max_words: int) -> list[dict]:
    """Режет нормативный акт по границам статей/пунктов: один чанк = одна статья.

    Зачем отдельно от chunk_sentences: тот режет по количеству слов, и в один
    чанк набивалось до ВОСЬМИ разных статей (замерено на живом индексе — ст. 309,
    310, 328, 330, 333, 395, 401, 421 в одном фрагменте на 4238 символов).
    Модель получала простыню и ссылалась не на ту статью: для неустойки 2% в день
    указала ст. 395 вместо ст. 333. Когда чанк — одна статья, поиск возвращает её
    как отдельную единицу, а номер статьи уезжает в метаданные и позволяет
    проверить ссылку модели.

    Возвращает список словарей `{"text": ..., "article": ...}`; `article` — это
    распознанный заголовок («Статья 333», «Пункт 17», «5.1.2») либо None, если
    разметку опознать не удалось.

    Статья длиннее max_words дробится внутри себя через chunk_sentences, все
    куски сохраняют номер своей статьи. Документ без распознаваемой разметки
    целиком уходит в chunk_sentences с article=None — то есть поведение не
    хуже прежнего.
    """
    if not text.strip():
        return []

    matches = list(_ARTICLE_BOUNDARY.finditer(text))
    if not matches:
        return [{"text": c, "article": None} for c in chunk_sentences(text, max_words)]

    out: list[dict] = []

    # Текст до первой статьи (преамбула, реквизиты приказа) — не теряем, но и
    # номера статьи у него нет.
    preamble = text[: matches[0].start()].strip()
    if preamble:
        out.extend({"text": c, "article": None} for c in chunk_sentences(preamble, max_words))

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start() : end].strip()
        if not body:
            continue
        label = " ".join(m.group(1).split()).rstrip(".")
        if len(body.split()) <= max_words:
            out.append({"text": body, "article": label})
        else:
            out.extend({"text": c, "article": label} for c in chunk_sentences(body, max_words))
    return out


def chunk_sentences(text: str, max_words: int, overlap_words: int = 0) -> list[str]:
    """Бьёт текст на чанки по границам предложений, ≤max_words слов каждый.

    overlap_words — сколько слов (примерно, целыми предложениями) с конца
    предыдущего чанка переносить в начало следующего, для контекстной
    непрерывности при поиске (RAG). 0 — чанки идут подряд без пересечения
    (для проверки орфографии, где дублирование правок не нужно).
    """
    if not text.strip():
        return []

    # Перенос не может быть больше половины лимита чанка — иначе почти
    # весь предыдущий чанк переезжает в следующий и толку от накопления
    # нового контента почти нет.
    overlap_words = min(overlap_words, max_words // 2)

    sentences: list[str] = []
    for sent in _split_sentences(text):
        n_words = len(sent.split())
        if n_words > max_words:
            sentences.extend(_split_long_sentence(sent, max_words))
        else:
            sentences.append(sent)

    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sent in sentences:
        sent_words = len(sent.split())
        if current and current_words + sent_words > max_words:
            chunks.append(" ".join(current))
            # Строгий бюджет: предложение переносится, только если ЦЕЛИКОМ
            # укладывается в оставшийся overlap_words — раньше первая (с
            # конца) фраза переносилась безусловно, из-за чего единственное
            # предложение крупнее бюджета (например, кусок принудительного
            # word-split'а) утаскивало в overlap весь предыдущий чанк целиком.
            carry_count = 0
            carry_words = 0
            for s in reversed(current):
                w = len(s.split())
                if carry_words + w > overlap_words:
                    break
                carry_count += 1
                carry_words += w
            current = current[-carry_count:] if carry_count else []
            current_words = carry_words
        current.append(sent)
        current_words += sent_words

    if current:
        chunks.append(" ".join(current))

    return chunks
