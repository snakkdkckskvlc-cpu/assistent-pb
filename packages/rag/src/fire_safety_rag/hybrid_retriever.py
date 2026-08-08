"""Гибридный поиск: векторный (ChromaDB) + лексический (BM25).

Зачем. Векторный поиск ищет по смыслу и на нашем корпусе плохо различает
близкие темы. Замерено на живом индексе (3334 чанка, multilingual-e5-large):

    запрос «неустойка явно несоразмерна последствиям нарушения»
      вектор → СП 484.1311500.2020 «Сигнализация», п. 3.24   (косинус 0.785)
      BM25   → ГК РФ, Статья 333 «Уменьшение неустойки»       (43.13, отрыв 4×)

«Статья 333» — это точное совпадение по номеру и слову, а не смысловая
близость; эмбеддер такое размывает, а BM25 берёт напрямую. Обратное тоже
верно: перефразированный пункт договора без общих слов с нормой находит
только вектор. Поэтому оба сигнала складываются, а не выбирается один.

Веса 0.6 вектор / 0.4 BM25 — сознательно в пользу вектора: лексика точнее
там, где совпадают термины, но слепа к синонимам.

Словоформы лексической половине больше не мешают: и корпус, и запрос идут
через лемматизацию pymorphy3 (см. _tokenize), поэтому «о взыскании
неустойки», «Уменьшение неустойки» и «неустойка» — один токен, а не три.
Пакета может не оказаться — тогда поиск работает по словоформам, как раньше.

Совместимость. Формат хита тот же, что у retriever.py (text/source/score/
act_number/article/doc_type/effective_date/status), плюс два новых поля —
`vector_score` (сырой косинус) и `bm25_score` (сырой BM25). Потребителю,
которому хватает `score`, менять ничего не нужно.
"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache

from . import config
from .retriever import Retriever

log = logging.getLogger(__name__)

# Токенизация: буквы и цифры, всё в нижний регистр. Цифры принципиально
# сохраняем — «333», «123-ФЗ», «5.13130» несут здесь основную различающую силу.
_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+")

_PASSAGE_PREFIX = "passage: "

# Служебные слова русского языка. Списком, а не через nltk.corpus.stopwords:
# тот тянет словарь из сети при первом обращении, а приложению выход за
# localhost запрещён (infrastructure/netguard.py) — работало бы на машине
# разработчика и падало у пользователя.
#
# Списком в строке, а не литералом set: сто сорок слов в одну строку кода
# нечитаемы, а править их придётся глазами.
_STOPWORDS_SOURCE = """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
    по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
    ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
    себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам
    чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому
    этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
    куда зачем всех никогда можно при наконец два об другой хоть после над больше
    тот через эти нас про всего них какая много разве три эту моя впрочем свою
    этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю
    между
"""
_STOPWORDS = frozenset(_STOPWORDS_SOURCE.split())


@lru_cache(maxsize=1)
def _morph():
    """Морфологический анализатор или None, если пакета нет.

    Мягкий отказ намеренно: pymorphy3 — не обязательное условие работы, а
    улучшение качества. Установщик Windows у этого проекта ломался уже не
    раз, и превращать поиск в ещё одну причину не запуститься нельзя. Без
    пакета BM25 работает ровно как раньше — по словоформам.
    """
    try:
        import pymorphy3
    except ImportError as e:
        log.warning("pymorphy3 не установлен (%s) — BM25 работает без лемматизации.", e)
        return None
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=200_000)
def _lemma(token: str) -> str:
    """Начальная форма слова. Кэш обязателен: корпус лемматизируется целиком.

    Зачем вообще. Для русского это не косметика: в договоре «о взыскании
    неустойки», в ГК «Уменьшение неустойки», в запросе «неустойка» — три
    РАЗНЫХ токена для BM25, и лексическая половина поиска их не связывает.
    Мы сами записали это в комментарии как известный недостаток.

    Токены с цифрами и латиницей проходят мимо анализатора: «333», «123»,
    «13130» — это номера статей и сводов правил, они и так в начальной форме,
    а pymorphy3 на них тратит время и иногда выдаёт неожиданное.
    """
    morph = _morph()
    if morph is None or not token.isalpha() or token.isascii():
        return token
    parsed = morph.parse(token)
    return parsed[0].normal_form if parsed else token


def _tokenize(text: str) -> list[str]:
    """Нижний регистр -> токены -> леммы -> отсев служебных слов.

    Стоп-слова убираются ПОСЛЕ лемматизации: «была» приводится к «быть», а в
    списке служебных есть и то и другое — иначе половина форм просачивалась
    бы обратно.

    Один и тот же конвейер обязан применяться и к корпусу, и к запросу:
    разойдись они, лексический поиск перестал бы находить вообще что-либо, и
    отказ был бы тихим — гибрид просто отдавал бы чисто векторную выдачу.
    """
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        token = _lemma(raw)
        if token in _STOPWORDS:
            continue
        out.append(token)
    return out


def _tokenize_query(text: str) -> list[str]:
    """Токены запроса БЕЗ повторов.

    BM25Okapi суммирует вклад каждого вхождения термина в запросе. На длинных
    запросах (пайплайн юр-анализа шлёт куски договора по 1500 символов) это
    ломает всё: строка из 24 повторов «рецепт борща сметана пампушки…»
    набирала 104.9 балла — больше, чем реальный кусок договора на осмысленном
    поиске, — просто потому, что предлоги повторялись два десятка раз. После
    дедупликации та же строка даёт 0.00.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in _tokenize(text):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _matches_where(meta: dict, where: dict | None) -> bool:
    """Проверяет метаданные чанка против where-фильтра ChromaDB.

    Нужен потому, что BM25 работает по выгруженному в память корпусу, а не
    через ChromaDB: без этой проверки лексическая половина выдачи игнорировала
    бы фильтры, которые векторная соблюдает, и в контекст возвращались бы
    отменённые редакции и технические СП там, где пайплайн их явно отсёк.

    Отсутствующий ключ трактуется так же, как это делает ChromaDB 0.5.23
    (проверено прямым запросом): под `$ne`/`$nin` документ БЕЗ поля проходит,
    под `$eq`/`$in` — нет.
    """
    if not where:
        return True
    for key, cond in where.items():
        if key == "$and":
            if not all(_matches_where(meta, c) for c in cond):
                return False
            continue
        if key == "$or":
            if not any(_matches_where(meta, c) for c in cond):
                return False
            continue
        present = key in meta
        value = meta.get(key)
        if not isinstance(cond, dict):
            if not present or value != cond:
                return False
            continue
        for op, operand in cond.items():
            if op == "$eq":
                ok = present and value == operand
            elif op == "$ne":
                ok = (not present) or value != operand
            elif op == "$in":
                ok = present and value in operand
            elif op == "$nin":
                ok = (not present) or value not in operand
            elif op == "$gt":
                ok = present and value > operand
            elif op == "$gte":
                ok = present and value >= operand
            elif op == "$lt":
                ok = present and value < operand
            elif op == "$lte":
                ok = present and value <= operand
            else:
                log.warning("Неизвестный оператор where: %s — условие пропущено", op)
                ok = True
            if not ok:
                return False
    return True


def _normalize_vector(values: list[float]) -> list[float]:
    """Косинус → 0..1 по ФИКСИРОВАННОЙ шкале, а не min-max по выдаче.

    Замерено на живом индексе, почему не min-max. У multilingual-e5-large
    косинусы сжаты в узкую полосу: заведомая чушь («рецепт борща со сметаной»)
    даёт 0.767, реальные куски договора — 0.814–0.862, точный запрос — 0.907.
    Min-max по выдаче растягивает разброс ВНУТРИ одной выдачи (например
    0.780–0.789) на весь диапазон 0..1 — то есть превращает девять тысячных,
    неотличимых от шума, в максимальный балл.

    Последствие было измерено: на запросе «неустойка явно несоразмерна…»
    ГК РФ ст. 333 находилась BM25 первой с отрывом в четыре раза, но получала
    0.4 (её потолок), а своды правил по сигнализации — 0.6 за счёт растянутого
    шума, и в выдачу ст. 333 не попадала вовсе. То есть ради чего добавляли
    BM25, то и ломалось.

    Шкала привязана к замеренному шумовому полу: 0.75 → 0, 0.95 → 1.
    """
    lo, hi = config.HYBRID_COSINE_FLOOR, config.HYBRID_COSINE_CEILING
    span = max(hi - lo, 1e-12)
    return [min(1.0, max(0.0, (v - lo) / span)) for v in values]


def _normalize_bm25(values: list[float]) -> list[float]:
    """BM25 → 0..1 с поправкой на абсолютную силу совпадения.

    Ноль здесь осмыслен сам по себе («ни одного общего термина»), поэтому
    нижнюю границу не сдвигаем — иначе худший кандидат всегда получал бы 0
    независимо от того, насколько он на самом деле хорош.

    Одного деления на максимум выдачи мало: тогда лучший лексический кандидат
    получает ровно 1.0 всегда, и запрос «рецепт борща» (BM25 = 8.4, ничего
    общего с корпусом) весит столько же, сколько точное попадание в ст. 333
    (43.1). Замерено: осмысленный запрос даёт максимум 37–110, бессмысленный —
    0–13. Поэтому вся лексическая половина дополнительно приглушается, пока
    максимум выдачи не дотягивает до HYBRID_BM25_SATURATION.
    """
    hi = max(values, default=0.0)
    if hi <= 1e-12:
        return [0.0] * len(values)
    strength = min(1.0, hi / config.HYBRID_BM25_SATURATION)
    return [(v / hi) * strength for v in values]


class HybridRetriever:
    """Векторный поиск ChromaDB + BM25 по тому же корпусу.

    Лексический индекс строится лениво при первом запросе из содержимого
    коллекции и кэшируется. Признак устаревания — число документов в
    коллекции: после переиндексации оно меняется, и индекс пересобирается.
    Этого достаточно, потому что переиндексация всегда либо добавляет чанки,
    либо пересоздаёт коллекцию; на всякий случай есть `reset()`.
    """

    def __init__(self, collection_name: str | None = None) -> None:
        self._vector = Retriever(collection_name)
        self._lock = threading.Lock()
        self._bm25 = None
        self._docs: list[str] = []
        self._metas: list[dict] = []
        self._key_index: dict[str, int] = {}
        self._built_for_count: int | None = None

    def is_ready(self) -> bool:
        return self._vector.is_ready()

    def reset(self) -> None:
        with self._lock:
            self._bm25 = None
            self._built_for_count = None

    def _ensure_bm25(self) -> bool:
        """Строит (или пересобирает) лексический индекс. False — если нельзя."""
        collection = self._vector._collection
        if collection is None:
            return False
        try:
            count = collection.count()
        except Exception as e:  # noqa: BLE001 — БД может быть недоступна
            log.warning("Не удалось узнать размер коллекции: %s", e)
            return False
        if count == 0:
            return False
        with self._lock:
            if self._bm25 is not None and self._built_for_count == count:
                return True
            try:
                from rank_bm25 import BM25Okapi
            except ImportError as e:
                log.warning("rank_bm25 не установлен (%s) — работает только векторный поиск.", e)
                return False
            try:
                data = collection.get(include=["documents", "metadatas"])
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось выгрузить корпус для BM25: %s", e)
                return False
            raw_docs = data.get("documents") or []
            metas = data.get("metadatas") or []
            docs = [
                d[len(_PASSAGE_PREFIX) :] if d and d.startswith(_PASSAGE_PREFIX) else (d or "")
                for d in raw_docs
            ]
            if not docs:
                return False
            self._docs = docs
            self._metas = [m or {} for m in metas]
            self._key_index = {
                self._key({**self._metas[i], "text": docs[i]}): i for i in range(len(docs))
            }
            self._bm25 = BM25Okapi([_tokenize(d) for d in docs])
            self._built_for_count = count
            log.info("BM25-индекс построен: %d чанков", len(docs))
            return True

    def _bm25_hit(self, index: int, raw_score: float) -> dict:
        meta = self._metas[index]
        return {
            "text": self._docs[index],
            "source": meta.get("source", "?"),
            "score": 0.0,
            "act_number": meta.get("act_number", ""),
            "article": meta.get("article", ""),
            "chapter": meta.get("chapter", ""),
            "doc_type": meta.get("doc_type", ""),
            "effective_date": meta.get("effective_date", ""),
            "status": meta.get("status", ""),
            "vector_score": 0.0,
            "bm25_score": raw_score,
        }

    def _bm25_scores(self, query: str):
        """Баллы BM25 по ВСЕМ чанкам корпуса для одного запроса (или None)."""
        if not self._ensure_bm25():
            return None
        tokens = _tokenize_query(query)
        if not tokens:
            return None
        # _ensure_bm25 выше гарантирует, что индекс построен, но связать эти
        # два факта проверяльщик типов не может — он видит только поле,
        # объявленное как None в __init__. Проверка явная, чтобы отказ был
        # понятной ошибкой, а не AttributeError где-то в недрах поиска.
        if self._bm25 is None:  # pragma: no cover — недостижимо после _ensure_bm25
            return None
        return self._bm25.get_scores(tokens)

    def _lexical_candidates(self, scores, where: dict | None, limit: int) -> list[dict]:
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out: list[dict] = []
        for i in ranked:
            if scores[i] <= 0:
                break
            if not _matches_where(self._metas[i], where):
                continue
            out.append(self._bm25_hit(i, float(scores[i])))
            if len(out) >= limit:
                break
        return out

    def search(self, query: str, top_k: int | None = None, where: dict | None = None) -> list[dict]:
        return self.search_many([query], top_k=top_k, where=where)[0]

    def search_many(
        self, queries: list[str], top_k: int | None = None, where: dict | None = None
    ) -> list[list[dict]]:
        if not queries:
            return []
        if not self.is_ready():
            return [[] for _ in queries]

        k = top_k or config.HYBRID_TOP_K
        # Кандидатов берём с запасом: половина отбора может прийти с другой
        # стороны, и обрезать до k надо ПОСЛЕ объединения, а не до.
        candidates = max(k * config.HYBRID_CANDIDATE_FACTOR, k)
        vector_groups = self._vector.search_many(queries, top_k=candidates, where=where)

        results: list[list[dict]] = []
        for query, vector_hits in zip(queries, vector_groups, strict=True):
            scores = self._bm25_scores(query)
            lexical_hits = (
                [] if scores is None else self._lexical_candidates(scores, where, candidates)
            )
            results.append(self._fuse(vector_hits, lexical_hits, k, scores))
        return results

    def _fuse(
        self, vector_hits: list[dict], lexical_hits: list[dict], k: int, scores
    ) -> list[dict]:
        """Сводит две выдачи в одну.

        Кандидату из векторной половины проставляется его НАСТОЯЩИЙ балл BM25:
        массив баллов по всему корпусу уже посчитан, и обнулять его только
        потому, что чанк не попал в лексический топ, значит врать. Обратное
        невозможно: векторный поиск не отдаёт score за пределами своего top-N,
        поэтому чисто лексическому кандидату вектор остаётся нулевым — при
        фиксированной шкале нормализации (см. _normalize_vector) это честно
        означает «ниже шумового пола», а не «худший из выдачи».
        """
        merged: dict[str, dict] = {}
        for hit in vector_hits:
            entry = dict(hit)
            entry["vector_score"] = float(hit.get("score", 0.0))
            idx = self._index_of(entry)
            entry["bm25_score"] = (
                float(scores[idx]) if scores is not None and idx is not None else 0.0
            )
            merged[self._key(entry)] = entry
        for hit in lexical_hits:
            key = self._key(hit)
            if key in merged:
                merged[key]["bm25_score"] = hit["bm25_score"]
            else:
                merged[key] = hit

        if not merged:
            return []

        items = list(merged.values())
        norm_vector = _normalize_vector([i["vector_score"] for i in items])
        norm_bm25 = _normalize_bm25([i["bm25_score"] for i in items])
        for item, nv, nb in zip(items, norm_vector, norm_bm25, strict=True):
            item["score"] = config.HYBRID_VECTOR_WEIGHT * nv + config.HYBRID_BM25_WEIGHT * nb
        items.sort(key=lambda i: i["score"], reverse=True)
        return items[:k]

    def _index_of(self, hit: dict) -> int | None:
        """Позиция чанка в выгруженном корпусе — чтобы взять его балл BM25."""
        return self._key_index.get(self._key(hit))

    @staticmethod
    def _key(hit: dict) -> str:
        """Один и тот же чанк из двух половин должен схлопнуться в один.

        ID чанка ChromaDB через `query()`/`get()` в наш формат хита не
        доезжает, поэтому ключ собирается из источника, номера статьи и
        начала текста — этого достаточно: два разных чанка одного акта с
        одинаковым первым сотней символов в корпусе не встречаются.
        """
        return f"{hit.get('source', '')}|{hit.get('article', '')}|{hit.get('text', '')[:100]}"


@lru_cache(maxsize=len(config.DOMAIN_COLLECTIONS))
def _hybrid_for(collection_name: str) -> HybridRetriever:
    """Ретривер на коллекцию. Кэш по имени, а не один на всё приложение:
    у каждого домена свой лексический индекс, и пересобирать его при каждом
    переключении домена значило бы выгружать корпус из ChromaDB заново.
    """
    return HybridRetriever(collection_name)


def retrieve_hybrid(
    queries: list[str],
    top_k: int | None = None,
    where: dict | None = None,
    domain: str | None = None,
) -> list[list[dict]]:
    """Батч-поиск, совместимый по сигнатуре с retrieve_many.

    domain выбирает корпус: «pb» — нормативка РФ, «nlmk» — документы
    заказчика. По умолчанию нормативка.
    """
    return _hybrid_for(config.collection_for_domain(domain)).search_many(
        queries, top_k=top_k, where=where
    )


def hybrid_ready(domain: str | None = None) -> bool:
    return _hybrid_for(config.collection_for_domain(domain)).is_ready()


__all__ = ["HybridRetriever", "hybrid_ready", "retrieve_hybrid"]
