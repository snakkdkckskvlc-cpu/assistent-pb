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

from pathlib import Path

import nltk

_NLTK_DATA_DIR = Path(__file__).resolve().parent / "resources" / "nltk_data"
if str(_NLTK_DATA_DIR) not in nltk.data.path:
    nltk.data.path.insert(0, str(_NLTK_DATA_DIR))


def _split_sentences(text: str) -> list[str]:
    return [s for s in nltk.tokenize.sent_tokenize(text, language="russian") if s.strip()]


def _split_long_sentence(sentence: str, max_words: int) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


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
