"""Настройки backend'а. Значения переопределяются через переменные окружения."""

from __future__ import annotations

import os
from pathlib import Path

# fire_safety_backend/ (пакет)
BASE_DIR = Path(__file__).resolve().parent

# Корень проекта: fire_safety_backend/ → src → backend → apps → корень
PROJECT_DIR = BASE_DIR.parent.parent.parent.parent

# Ресурсы упакованы внутрь пакета
RESOURCES_DIR = BASE_DIR / "resources"
PROMPTS_DIR = RESOURCES_DIR / "prompts"
LETTERHEAD_TEMPLATE = RESOURCES_DIR / "templates" / "letterhead.docx"

# Frontend теперь в apps/desktop/frontend
FRONTEND_DIR = PROJECT_DIR / "apps" / "desktop" / "frontend"

# Runtime-данные общие на весь проект
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = DATA_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
# Расшифрованные копии документов на время обработки. Именно ЗДЕСЬ, а не в
# системном %TEMP%: открытая копия договора не должна оставаться там, куда не
# дотягивается автоочистка (services/retention.py).
WORK_DIR = DATA_DIR / "tmp"
WORK_DIR.mkdir(exist_ok=True)


def _env_flag(name: str, default: bool) -> bool:
    """Булев флаг из окружения. Пусто/не задано — значение по умолчанию."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _default_llm_model() -> str:
    """qwen2.5:7b-instruct — модель, которую bootstrap.ps1 фактически качает по
    умолчанию. Если при установке был выбран другой LLM_MODEL, bootstrap.ps1
    записывает его в data/llm_model.txt — читаем оттуда, чтобы поведение не
    зависело от того, как именно запущено приложение (ярлык/start.bat/IDE)."""
    model_file = DATA_DIR / "llm_model.txt"
    if model_file.exists():
        name = model_file.read_text(encoding="utf-8").strip()
        if name:
            return name
    return "qwen2.5:7b-instruct"


def _total_ram_gb() -> float:
    """Сколько всего ОЗУ на машине. 0.0 — определить не удалось."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        pass
    try:  # Windows
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullTotalPhys / 1e9
    except Exception:  # noqa: BLE001 — на не-Windows это просто не сработает
        return 0.0


def _auto_num_ctx_legal() -> int:
    """Окно юр. анализа под фактическую память машины.

    Зачем автоматически. Одна и та же сборка ставится и на машину
    разработчика (8 ГБ), и на боевой сервер (Ryzen 5 5600, 128 ГБ). Жёсткое
    значение под слабую машину обесценивает сильную, а под сильную — вешает
    слабую: замерено, что на 8 ГБ при окне 16384 KV-кэш вытеснил всё в swap
    (7 ГБ свопа при 8 ГБ памяти) и анализ замедлился примерно в восемь раз.

    Расчёт памяти: сама модель ~5 ГБ (7B в Q4_K_M) плюс KV-кэш, у qwen2.5-7B
    это ~56 КБ на токен окна, то есть 32768 ≈ +1,8 ГБ. Плюс запас на ОС и
    остальное приложение. Пороги ниже с этим запасом и выставлены.

    Значение можно переопределить переменной LLM_NUM_CTX_LEGAL — автоподбор
    тогда не применяется вовсе.
    """
    ram = _total_ram_gb()
    if ram <= 0:
        return 8192  # не смогли определить — берём заведомо безопасное
    if ram >= 64:
        # Договор на 17 страниц влезает одним запросом, дробление не нужно.
        return 32768
    if ram >= 32:
        return 16384
    if ram >= 12:
        return 12288
    return 8192


# --- LLM (Ollama) ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("LLM_MODEL") or _default_llm_model()
LLM_TIMEOUT_SEC = int(os.environ.get("LLM_TIMEOUT_SEC", "900"))


def _parse_keep_alive(raw: str) -> int | str:
    """Ollama принимает либо ЧИСЛО секунд (-1 = держать бессрочно), либо строку
    с единицей измерения («30m», «24h»). Голая строка "-1" отвергается с
    `time: missing unit in duration` — поэтому целые числа отдаём именно
    числом, а не строкой из переменной окружения."""
    try:
        return int(raw)
    except ValueError:
        return raw


# Сколько Ollama держит модель в памяти после запроса. Дефолт Ollama — 5
# минут, после чего модель выгружается и следующий запрос заново читает её
# с диска (замерено: холодный запрос 9.3 c против 1.2 c тёплого — восемь
# секунд впустую). Инструментом пользуются несколько раз в день, поэтому
# «после паузы» — это почти каждый запрос. -1 — держать бессрочно; модель
# занимает ~5 ГБ при 128 ГБ ОЗУ на целевом сервере.
LLM_KEEP_ALIVE = _parse_keep_alive(os.environ.get("LLM_KEEP_ALIVE", "-1"))

# Число потоков llama.cpp. По умолчанию не задаём — Ollama выбирает сама, и
# для однородных процессоров (как Ryzen 5 5600 на боевом сервере) её выбор
# обычно верный. Настройка нужна для ГИБРИДНЫХ процессоров (Intel 12-го
# поколения и новее: быстрые P-ядра + медленные E-ядра): там работа делится
# между ядрами поровну, и быстрые простаивают в ожидании медленных. Замер на
# Core Ultra 5 125H (14 ядер / 18 потоков): дефолт 6.96 ток/с, 12 потоков
# 7.79 ток/с (+12%), 4 потока 5.76 ток/с. Оптимум зависит от железа —
# подбирается замером на конкретной машине, а не переносится из чужой.
_raw_threads = os.environ.get("LLM_NUM_THREAD", "").strip()
LLM_NUM_THREAD: int | None = int(_raw_threads) if _raw_threads.isdigit() else None
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_NUM_CTX = int(os.environ.get("LLM_NUM_CTX", "4096"))
# Отдельное окно для юр. анализа: договор целиком туда всё равно не влезает
# (17 страниц ≈ 16 000 токенов), поэтому pipelines/legal.py режет его на части
# ПО ЭТОМУ ЗНАЧЕНИЮ.
#
# Значение подбирается ПО ФАКТИЧЕСКОЙ ПАМЯТИ машины — см. _auto_num_ctx_legal.
# Раньше здесь стояло жёсткое 8192, выбранное под 8 ГБ машины разработчика, и
# боевой сервер со 128 ГБ работал в том же тесном режиме: договор дробился на
# восемь частей вместо одной, а платим мы за каждый ВЫДАННЫЙ токен (замер на
# этом железе: чтение промпта 165–260 токенов/с, генерация 11–12,5).
# Восемь частей по 1800 токенов ответа — это 14 400 токенов генерации вместо
# 3 500 при одном запросе, то есть примерно вчетверо дольше на ровном месте.
LLM_NUM_CTX_LEGAL = int(os.environ.get("LLM_NUM_CTX_LEGAL", "0")) or _auto_num_ctx_legal()
LLM_NUM_PREDICT_SPELLCHECK = 1500
# Перечисление ошибок — не творческая задача: на один и тот же текст ответ
# обязан быть один и тот же. При общей LLM_TEMPERATURE=0.2 замер на размеченном
# наборе давал то 19, то 18 находок из 22 — разброс не от правок, а от
# сэмплирования, и на нём нельзя отличить улучшение от шума.
LLM_TEMPERATURE_SPELLCHECK = float(os.environ.get("LLM_TEMPERATURE_SPELLCHECK", "0"))
LLM_NUM_PREDICT_LEGAL = 3500
# Резерв под ответ на ОДНУ часть договора. Меньше общего LLM_NUM_PREDICT_LEGAL:
# на части приходится и находок меньше, а каждый зарезервированный токен ответа
# отнимается от окна и делает часть мельче (то есть увеличивает их число).
LLM_NUM_PREDICT_LEGAL_PART = 1800
LLM_NUM_PREDICT_LETTER = 1500

# --- OCR ---
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")  # None → авто из PATH
TESSERACT_LANG = "rus+eng"
# EasyOCR — необязательная замена Tesseract. Ставится отдельно
# (`pip install easyocr`), тянет torch, работает на CPU. Если пакет не
# установлен, всё продолжает работать на Tesseract.
USE_EASYOCR = os.environ.get("USE_EASYOCR", "1") not in {"0", "false", "False"}
EASYOCR_LANGS = ("ru", "en")
# Ниже этой уверенности распознанный фрагмент заменяется на [?]. Показать
# явную дыру честнее, чем подсунуть в договор правдоподобное, но выдуманное
# слово: юрист увидит [?] и сверится с бумагой, а «г. Линецк» пропустит.
EASYOCR_MIN_CONFIDENCE = float(os.environ.get("EASYOCR_MIN_CONFIDENCE", "0.6"))

# --- LanguageTool (офлайн-проверка грамматики/пунктуации, доп. к LLM) ---
# Отдельный локальный процесс (tools/languagetool/start.sh), не часть backend'а —
# так же, как Ollama. Недоступен — просто идём только на LLM (см.
# infrastructure/languagetool.py::check).
LANGUAGETOOL_HOST = os.environ.get("LANGUAGETOOL_HOST", "http://127.0.0.1:8081")
LANGUAGETOOL_TIMEOUT_SEC = float(os.environ.get("LANGUAGETOOL_TIMEOUT_SEC", "20"))
# Поднимать ли сервер самим при старте. У Ollama есть собственная служба
# Windows, у LanguageTool её нет — и пока запуск был на человеке, он не
# происходил никогда, а каждая проверка орфографии уходила в модель. Выключать
# имеет смысл, если сервер поднимают отдельно (служба, systemd, вручную).
LANGUAGETOOL_AUTOSTART = _env_flag("LANGUAGETOOL_AUTOSTART", True)

# --- Chunking для длинных текстов при spellcheck ---
# Порция текста на ОДИН запрос к модели. Не производительность, а качество:
# замерено на 19 намеренно заложенных ошибках, одна модель и один промпт,
# менялся только размер куска —
#     300 слов (≈20 предложений)  →  5 из 19
#     ≈4 предложения              →  9 из 14 на тех же пропущенных
#     1 предложение               → 11 из 14 на тех же пропущенных
# На большом куске модель находит две-три ошибки и останавливается, пропуская
# даже «обьекте» и «в течении». По времени разница мала: платим за выданные
# токены, а их столько же. 25 слов — это одно-два предложения делового текста.
SPELLCHECK_CHUNK_WORDS = 25

# --- Загрузка файлов ---
# Потолок на один файл. Приходящий файл читается в память целиком, поэтому
# без потолка случайно перетащенный многогигабайтный файл (архив, видео)
# укладывает backend без внятного сообщения. 64 МБ с запасом покрывают
# реальные документы: договор на 17 страниц со сканами — единицы мегабайт.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))


# --- Защита данных на диске ---
# Через приложение проходят договоры контрагентов и письма компании, а
# разграничения доступа нет: кто получил файлы — получил все документы.
# Поэтому uploads/outputs хранятся зашифрованными Windows DPAPI (см.
# infrastructure/dpapi.py и infrastructure/secure_files.py). Выключать имеет
# смысл только при отладке.
ENCRYPT_AT_REST = _env_flag("ENCRYPT_AT_REST", True)

# Сколько дней жить загруженным и сгенерированным файлам. Шифрование не
# спасает от кода, запущенного под этой же учётной записью Windows, — а вот
# отсутствие файла спасает. Скачанное пользователем лежит там, куда он его
# сохранил, и очисткой не затрагивается. 0 — не удалять ничего.
DATA_RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "7"))
# Как часто проверять сроки, пока приложение открыто. Раз в 6 часов: рабочий
# день длиннее, чем интервал, поэтому долгую сессию очистка тоже накрывает.
DATA_RETENTION_SWEEP_SEC = int(os.environ.get("DATA_RETENTION_SWEEP_SEC", str(6 * 60 * 60)))
