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

# --- Chunking для длинных текстов при spellcheck ---
SPELLCHECK_CHUNK_WORDS = 300
