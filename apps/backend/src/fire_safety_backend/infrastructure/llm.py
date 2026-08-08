"""Клиент к Ollama. Тонкая обёртка над /api/chat с JSON-режимом."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from .. import config

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# Общий на всё приложение клиент (lifespan-scoped) — избегаем открытия
# нового TCP-соединения к Ollama на каждый вызов. connect-таймаут короткий,
# чтобы "Ollama не запущена" падало за секунды, а не ждало все 15 минут,
# отведённых на долгую генерацию (read-таймаут).
_TIMEOUT = httpx.Timeout(connect=5.0, read=float(config.LLM_TIMEOUT_SEC), write=30.0, pool=5.0)
_client: httpx.AsyncClient | None = None


def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=_TIMEOUT)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("LLM-клиент не запущен — вызовите llm.startup() в lifespan")
    return _client


def _sec(stats: dict[str, Any], key: str) -> float:
    """Длительность из ответа Ollama — она приходит в НАНОСЕКУНДАХ."""
    value = stats.get(key)
    return value / 1e9 if isinstance(value, (int, float)) else 0.0


def _log_stats(stats: dict[str, Any], effective_ctx: int) -> None:
    """Разложение времени запроса по фазам.

    Зачем отдельной строкой в логе, а не «замерим, когда понадобится». Юр.
    анализ договора идёт 40 минут, а по расчёту должен около 18: чтение
    промпта 18–20 тыс. токенов при 165–260 т/с плюс генерация ~12 000 при
    12 т/с. Пока в логе одно число — сколько токенов прочитано, — непонятно,
    где именно теряются недостающие двадцать минут, и любая оптимизация идёт
    вслепую. Ollama отдаёт разбивку в том же чанке, что и prompt_eval_count,
    и стоит она ноль.

    Отдельно считается «прочее»: total минус загрузка, чтение и генерация.
    Если оно велико, время уходит не на арифметику модели (сэмплинг,
    softmax на длинном контексте, накладные расходы Ollama), и это ровно тот
    случай, когда крутить num_thread бессмысленно.
    """
    prompt_tokens = stats.get("prompt_eval_count")
    if prompt_tokens is None:
        return

    load = _sec(stats, "load_duration")
    read = _sec(stats, "prompt_eval_duration")
    write = _sec(stats, "eval_duration")
    total = _sec(stats, "total_duration")
    out_tokens = stats.get("eval_count") or 0
    other = max(0.0, total - load - read - write)

    log.info(
        "LLM тайминг: чтение %d ток за %.1f с (%.0f ток/с), генерация %d ток за %.1f с "
        "(%.1f ток/с), загрузка %.1f с, прочее %.1f с, всего %.1f с",
        prompt_tokens,
        read,
        prompt_tokens / read if read > 0 else 0.0,
        out_tokens,
        write,
        out_tokens / write if write > 0 else 0.0,
        load,
        other,
        total,
    )

    log.info("LLM prompt: обработано %d токенов из окна %d", prompt_tokens, effective_ctx)
    if prompt_tokens >= effective_ctx * 0.95:
        log.error(
            "LLM: промпт (%d токенов) вплотную к окну %d — вход почти наверняка "
            "обрезан, модель видела не весь запрос",
            prompt_tokens,
            effective_ctx,
        )


async def chat(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    on_delta: Callable[[str], None] | None = None,
    model: str | None = None,
) -> str:
    """Вызов чата. Возвращает строку с полным ответом модели.

    on_delta, если передан, включает потоковый режим у Ollama (stream: true)
    — колбэк вызывается на каждый полученный текстовый фрагмент. Используется
    только как индикатор прогресса (полоса загрузки в UI, см.
    pipelines/_prompts.py::make_progress_counter) — полноценный посимвольный
    рендер в UI осознанно не делаем: все пайплайны возвращают структурный
    JSON (json_mode=True), а частичный JSON нельзя осмысленно показать до
    завершения генерации. Итоговый текст возвращается целиком в обоих режимах."""
    options: dict[str, Any] = {
        "temperature": temperature if temperature is not None else config.LLM_TEMPERATURE,
        "num_ctx": num_ctx if num_ctx is not None else config.LLM_NUM_CTX,
    }
    if num_predict is not None:
        options["num_predict"] = num_predict
    if config.LLM_NUM_THREAD is not None:
        options["num_thread"] = config.LLM_NUM_THREAD
    model = model or config.LLM_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "stream": on_delta is not None,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": options,
        # Ollama по умолчанию выгружает модель через 5 минут простоя, и
        # следующий запрос заново читает ~5 ГБ с диска. Замерено на боевой
        # конфигурации: холодный запрос 9.3 c против 1.2 c тёплого — восемь
        # секунд впустую. Для офисного инструмента, которым пользуются
        # несколько раз в день, «холодным» оказывается почти каждый запрос.
        # Держать модель в памяти дешевле любой другой оптимизации: на
        # целевом сервере 128 ГБ ОЗУ, модель занимает около 5 ГБ.
        "keep_alive": config.LLM_KEEP_ALIVE,
    }
    if json_mode:
        payload["format"] = "json"

    url = f"{config.OLLAMA_HOST}/api/chat"
    log.info(
        "LLM chat → %s (json=%s, stream=%s, chars=%d)",
        model,
        json_mode,
        on_delta is not None,
        len(user),
    )

    if on_delta is None:
        try:
            r = await _get_client().post(url, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        data = r.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"Empty response from Ollama: {data}")
        _log_stats(data, options["num_ctx"])
        return content

    content_parts: list[str] = []
    stats: dict[str, Any] = {}
    try:
        async with _get_client().stream("POST", url, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    content_parts.append(delta)
                    on_delta(delta)
                # Финальный чанк несёт статистику: сколько токенов промпта
                # Ollama РЕАЛЬНО обработала. Единственный доступный нам способ
                # узнать, не обрезала ли она вход: при превышении num_ctx она
                # молча отбрасывает начало запроса (вместе с системным
                # промптом) и ничего об этом не сообщает.
                if chunk.get("prompt_eval_count") is not None:
                    stats = chunk
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama request failed: {e}") from e

    _log_stats(stats, options["num_ctx"])

    content = "".join(content_parts)
    if not content:
        raise LLMError("Empty streamed response from Ollama")
    return content


async def chat_json(system: str, user: str, *, retries: int = 1, **kwargs) -> dict:
    """Вызов с JSON-режимом + парсинг, с одним повтором на неисправимый ответ.

    Две ступени, потому что дефекты формата бывают двух разных сортов.

    Первая — разбор ответа (`_parse_json_loose`): переименование ключей и
    починка типовых синтаксических поломок. Стоит ноль секунд и закрывает
    случаи, которые повтор НЕ закрыл бы вовсе — например, английские имена
    полей при полностью валидном JSON: повторять там нечего, ошибки нет.

    Вторая — этот повтор. Нужен потому, что поломки не сводятся к списку
    известных: замерено на GigaChat3.1-10B, договор 01 — в одном прогоне
    английские ключи, в другом синтаксическая ошибка в другом месте.

    Повтор осмыслен даже при `temperature=0`: ответы модели МЕЖДУ ПРОГОНАМИ
    различаются (обычное дело для CPU-инференса — разное разбиение батчей
    меняет порядок сложений в плавающей арифметике). Это проверено на том же
    договоре 01: два прогона с одним промптом дали разные ответы и разные
    ошибки.

    Цена честная: повтор удваивает время вызова. Поэтому он ОДИН, и только
    когда починить не удалось. Отказ после шести минут ожидания дороже.
    """
    for attempt in range(retries + 1):
        raw = await chat(system, user, json_mode=True, **kwargs)
        try:
            return _parse_json_loose(raw)
        except LLMError:
            if attempt >= retries:
                raise
            log.warning(
                "Ответ модели не разобрать даже после починки — повторяю запрос (попытка %d из %d)",
                attempt + 2,
                retries + 1,
            )
    raise LLMError("Недостижимо: цикл повторов завершился без результата")


# Модель может назвать поля по-английски, хотя схема в промпте русская.
# Замерено на GigaChat3.1-10B, договор 01: ответ пришёл СМЕШАННЫМ — находки с
# ключами findings/criticality/quote_from_contract, а сводка тут же рядом с
# русскими «плюсы_для_компании». Разбор был содержательный, пять находок, но
# пайплайн ищет ключ «находки», не находит и отдаёт ПУСТОЙ результат. Для
# пользователя это выглядит как «в договоре рисков нет» — то есть худший из
# возможных отказов: тихий и правдоподобный.
#
# Поэтому имена ключей нормализуются к схеме промпта. Список закрытый: только
# прямые переводы полей наших схем, никаких догадок.
_KEY_SYNONYMS = {
    # юр. анализ
    "findings": "находки",
    "criticality": "критичность",
    "severity": "критичность",
    "quote_from_contract": "цитата_из_договора",
    "quote": "цитата_из_договора",
    "risk": "в_чём_риск",
    "link_to_norm": "ссылка_на_норму",
    "norm_reference": "ссылка_на_норму",
    "source_fragment": "источник_фрагмента",
    "edit_suggestion": "предложение_правки",
    "suggestion": "предложение_правки",
    "summary": "сводка",
    "pros": "плюсы_для_компании",
    "cons": "минусы_для_компании",
    "conclusion": "общий_вывод",
}


def _normalize_keys(value: Any) -> Any:
    """Переименовывает английские ключи в русские по закрытому списку.

    Существующий русский ключ НЕ затирается: если модель прислала оба, верным
    считается тот, что назван по схеме.
    """
    if isinstance(value, list):
        return [_normalize_keys(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, val in value.items():
        target = _KEY_SYNONYMS.get(key, key)
        if target != key and target in value:
            target = key
        out[target] = _normalize_keys(val)
    return out


def _repair_json(text: str) -> str | None:
    """Чинит одиночные синтаксические дефекты. None — если чинить нечего.

    Не «умный ремонт» произвольного мусора, а два узких правила под то, что
    модели реально ломают. Каждое проверяется повторным парсингом, поэтому
    неудачная починка ничего не портит: она просто не применится.

    Правило 1 — ЛИШНЯЯ КАВЫЧКА ПЕРЕД КЛЮЧОМ. Замерено на GigaChat3.1-10B,
    договор 05: `}],""сводка": {` вместо `}],"сводка": {`. Ответ при этом
    целый, генерация не обрывалась — весь разбор терялся из-за одного знака.
    Паттерн намеренно требует ключ и двоеточие после него: `,""` внутри
    массива строк («минусы»: ["a", ""]) — законная запись, и её трогать
    нельзя.

    Правило 2 — висячая запятая перед закрывающей скобкой: JSON её не
    допускает, а модели ставят по привычке из JavaScript.
    """
    repaired = re.sub(r'([,{\[])\s*""(\w[^"]*)"\s*:', r'\1"\2":', text)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired if repaired != text else None


def _fix_early_root_close(text: str) -> str | None:
    """Модель закрыла корневой объект и продолжила писать ключи.

    Замерено на GigaChat3.1-10B, договор 05: после сводки идёт `}},` и дальше
    ещё один ключ верхнего уровня. Корень закрылся на скобку раньше, чем
    модель закончила, — остаток документа становится «Extra data».

    Чинится СТРОГО по позиции, которую сообщил парсер: лишняя скобка убирается
    там, где JSON фактически закончился. Слепая замена `}},"` на `},"` была бы
    ошибкой — это совершенно законная запись (`{"a":{"b":{}},"c":1}`), и такой
    «ремонт» ломал бы верные ответы.
    """
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        if not e.msg.startswith("Extra data"):
            return None
        head, tail = text[: e.pos].rstrip(), text[e.pos :]
        if not head.endswith("}") or not tail.lstrip().startswith(","):
            return None
        return head[:-1] + tail
    return None


def _parse_json_loose(text: str) -> dict:
    text = text.strip()
    # Модель иногда всё же оборачивает в ```json ... ```
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    candidates = [text]
    # Попробовать найти первую '{' и последнюю '}'
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])

    error: json.JSONDecodeError | None = None
    for candidate in candidates:
        # Починки применяются по очереди и НАКАПЛИВАЮТСЯ: на договоре 05 из
        # замера сначала убирается лишняя кавычка перед ключом, и только после
        # этого становится виден рано закрытый корень. По одной ни та, ни
        # другая правка ответ не спасала.
        variants = [candidate]
        repaired = _repair_json(candidate)
        if repaired is not None:
            variants.append(repaired)
        reattached = _fix_early_root_close(variants[-1])
        if reattached is not None:
            variants.append(reattached)

        for attempt, variant in enumerate(variants):
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError as e:
                error = e
                continue
            if attempt:
                log.warning(
                    "JSON модели был повреждён и восстановлен (%d правк%s): %s",
                    attempt,
                    "а" if attempt == 1 else "и",
                    error,
                )
            return _normalize_keys(parsed) if isinstance(parsed, dict) else parsed

    raise LLMError(f"Модель вернула невалидный JSON: {error}\n---\n{text[:500]}") from error


async def healthcheck() -> dict:
    """Проверка что Ollama запущена и модель доступна."""
    try:
        r = await _get_client().get(f"{config.OLLAMA_HOST}/api/tags", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Ollama недоступна: {e}"}
    tags = r.json().get("models", [])
    names = [m.get("name", "") for m in tags]

    # Проверяются ВСЕ модели, а не только LLM_MODEL. Пока модель была одна,
    # разницы не было; с тех пор как юр. анализ пошёл на своей, проверка по
    # одной давала зелёный health при отсутствующей второй — и задача падала
    # уже в работе, после того как пользователь её отправил.
    missing = [m for m in config.used_models() if not _is_installed(m, names)]
    warning = (
        _memory_warning(tags)
        if not missing
        else "Не установлены модели: " + "; ".join(f"{m} (ollama pull {m})" for m in missing)
    )
    return {
        "ok": not missing,
        "model": config.LLM_MODEL,
        "models": config.used_models(),
        "installed": names,
        "warning": warning,
    }


def _is_installed(model: str, names: list[str]) -> bool:
    """Есть ли модель среди установленных.

    Сверка нестрогая: Ollama возвращает имя с тегом, а в конфиге он может быть
    опущен. Обратное тоже бывает — в имени из hf.co двоеточие отделяет
    квантизацию (`hf.co/…-GGUF:Q4_K_M`), поэтому «часть до двоеточия» здесь
    не имя семейства, а путь, и сравнивать надо и так, и так.
    """
    head = model.split(":")[0]
    return any(n == model or n.startswith(model + ":") or n.startswith(head + ":") for n in names)


# Сверх веса модели нужна память под KV-кэш, контекст и саму ОС. Коэффициент
# грубый, но он и не должен быть точным: задача — отличить «влезает с запасом»
# от «не влезет никогда», а не выгадать последний гигабайт.
_MODEL_MEMORY_HEADROOM = 1.4


def _memory_warning(tags: list[dict]) -> str | None:
    """Предупреждение, если выбранная модель не помещается в ОЗУ.

    Симптом нехватки памяти — не ошибка, а бесконечное ожидание: модель
    вытесняется в swap и генерация замедляется на порядки. Замерено на машине
    разработчика: 8,6 ГБ ОЗУ против модели на 18,6 ГБ — своп 7,8 ГБ, процессы
    llama-server вытеснены с диска целиком, ответ не приходит вообще.
    Без этой проверки пользователь видит «задача выполняется» и ждёт часами.

    Считается СУММА по всем используемым моделям, а не вес одной. При
    `LLM_KEEP_ALIVE=-1` Ollama держит в памяти каждую, к которой обращались, и
    с разными моделями на юр. анализ и орфографию резидентными оказываются обе
    (qwen2.5 ~4,7 ГБ + GigaChat ~7 ГБ). Проверка по одной модели пропустила бы
    ровно тот случай, ради которого написана.
    """
    by_name = {m.get("name", ""): m.get("size", 0) for m in tags}
    sizes = {
        model: size
        for model in config.used_models()
        if (size := _installed_size(model, by_name)) > 0
    }
    total = sum(sizes.values())
    ram = config._total_ram_gb() * 1e9
    if not total or ram <= 0:
        return None
    if total * _MODEL_MEMORY_HEADROOM <= ram:
        return None

    if len(sizes) == 1:
        model, size = next(iter(sizes.items()))
        what = f"Модель {model} весит {size / 1e9:.1f} ГБ"
    else:
        parts = ", ".join(f"{m} — {s / 1e9:.1f} ГБ" for m, s in sizes.items())
        what = (
            f"Приложению нужны {len(sizes)} модели одновременно ({parts}), "
            f"вместе {total / 1e9:.1f} ГБ"
        )
    return (
        f"{what}, а на машине {ram / 1e9:.1f} ГБ ОЗУ. В память они не помещаются: "
        f"ответы будут идти из swap, то есть в десятки раз медленнее или не придут "
        f"вовсе. Возьмите модель меньше или добавьте оперативной памяти."
    )


def _installed_size(model: str, by_name: dict[str, int]) -> int:
    return next(
        (size for name, size in by_name.items() if name == model or name.startswith(model + ":")),
        0,
    )
