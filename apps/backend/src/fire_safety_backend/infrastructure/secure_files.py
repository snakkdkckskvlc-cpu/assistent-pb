"""Прозрачное шифрование рабочих файлов на диске.

Через приложение проходят договоры контрагентов и письма компании, а
разграничения доступа нет — значит защита возможна только на уровне самих
файлов. Здесь она и живёт: всё, что попадает в data/uploads и data/outputs,
лежит зашифрованным ключом учётной записи Windows (см. dpapi.py).

Формат файла: MAGIC + DPAPI-блоб. Магия нужна, чтобы отличать наш конверт от
файла, оставшегося открытым с прошлых версий, — старые файлы продолжают
читаться, а не превращаются в «повреждённый документ».

Имя: к логическому имени дописывается `.enc` (`договор.docx` →
`договор.docx.enc`). Не «то же имя, но нечитаемое содержимое»: двойной клик по
такому файлу в проводнике дал бы «Word не может открыть документ» без всяких
объяснений, а `.enc` хотя бы говорит, что происходит.

Наружу модуль отдаёт ЛОГИЧЕСКИЕ пути (без `.enc`): и парсеры (выбирают
обработчик по `path.suffix`), и генератор исправленного документа (берёт
`source_path.stem` для имени результата) рассчитывают на настоящее имя файла.
`.enc` — деталь хранения, она не должна протекать в остальной код.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .. import config
from . import dpapi, file_access

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger(__name__)

# Версия в магии — чтобы при смене формата было что проверить, а не гадать.
MAGIC = b"APBENC1\0"
STORED_SUFFIX = ".enc"

# Дополнительная entropy для DPAPI. Не секрет (лежит в этом же коде), но
# привязывает блоб к нашему приложению: сторонний инструмент, который просто
# перебирает DPAPI-блобы в профиле пользователя, наши файлы не раскроет.
_ENTROPY = b"assistent-pb:v1"


class DecryptError(RuntimeError):
    """Файл не расшифровывается. Сообщение предназначено пользователю."""


class StorageUnprotected(RuntimeError):
    """Шифрование обязано работать, но не работает — писать отказываемся."""


class Protector(Protocol):
    """Минимум, который нужен слою: зашифровать и расшифровать байты."""

    name: str

    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class _DpapiProtector:
    name = "dpapi"

    def protect(self, data: bytes) -> bytes:
        return dpapi.protect(data, _ENTROPY)

    def unprotect(self, data: bytes) -> bytes:
        return dpapi.unprotect(data, _ENTROPY)


@dataclass(frozen=True)
class Status:
    """Состояние шифрования: чем шифруем, почему не шифруем, и это авария или нет."""

    mode: str  # "dpapi" | "off"
    reason: str  # человекочитаемое пояснение для интерфейса и логов
    broken: bool  # шифрование ожидается, но недоступно → писать нельзя


_status: Status | None = None
_protector: Protector | None = None


def _resolve() -> tuple[Status, Protector | None]:
    if not config.ENCRYPT_AT_REST:
        # Осознанное решение оператора, а не сбой: пишем открытым текстом, но
        # так, чтобы это было видно в логе и в интерфейсе.
        log.warning("Шифрование на диске ВЫКЛЮЧЕНО (ENCRYPT_AT_REST=0)")
        return Status(
            "off", "выключено в настройках — документы лежат незашифрованными", False
        ), None

    if dpapi.is_available():
        return Status(
            "dpapi", "включено средствами Windows, ключ привязан к учётной записи", False
        ), _DpapiProtector()

    if sys.platform == "win32":
        # Аномалия: платформа та самая, шифрование обещано — а не работает.
        # Чаще всего это повреждённые master-ключи DPAPI после сброса пароля.
        # Тихо ронять договоры на диск открытым текстом в такой ситуации
        # нельзя: именно так пропажа фирменного бланка и осталась незамеченной.
        log.error(
            "Шифрование включено, но DPAPI недоступен — файлы НЕ будут "
            "сохраняться. Проверьте учётную запись Windows.",
        )
        return Status(
            "off",
            "Windows не отдаёт ключ этой учётной записи — документы НЕ сохраняются. "
            "Покажите этот экран администратору компьютера",
            True,
        ), None

    # Не Windows: DPAPI тут не существует в принципе. Это разработка или CI,
    # а не боевая машина, поэтому не авария — но предупредить надо.
    log.warning("DPAPI есть только на Windows — файлы хранятся открытым текстом")
    return Status(
        "off",
        "шифрование работает только на Windows, а это другая система — "
        "документы лежат незашифрованными",
        False,
    ), None


def status() -> Status:
    global _status, _protector
    if _status is None:
        _status, _protector = _resolve()
    return _status


def protector() -> Protector | None:
    status()
    return _protector


def use_protector(p: Protector | None, *, mode: str = "test", broken: bool = False) -> None:
    """Подменить протектор. Нужно тестам: на Linux (CI) DPAPI не существует,
    а логику конверта проверять надо именно там, где гоняются тесты."""
    global _status, _protector
    _protector = p
    _status = Status(mode if p else "off", "подменено в тесте", broken)


def reset() -> None:
    """Сбросить кеш — пересчитается при следующем обращении."""
    global _status, _protector
    _status, _protector = None, None


# --- Пути ---


def encrypted_path(logical: Path) -> Path:
    """`договор.docx` → `договор.docx.enc`."""
    return logical.with_name(logical.name + STORED_SUFFIX)


def stored_path(logical: Path) -> Path | None:
    """Где на самом деле лежит файл: зашифрованный или оставшийся открытым.

    Зашифрованный имеет приоритет: если рядом почему-то лежат оба, открытый —
    это остаток, который store() должен был удалить.
    """
    enc = encrypted_path(logical)
    if enc.exists():
        return enc
    if logical.exists():
        return logical
    return None


def exists(logical: Path) -> bool:
    return stored_path(logical) is not None


# --- Чтение и запись ---


def _wrap(data: bytes, p: Protector) -> bytes:
    return MAGIC + p.protect(data)


def _unwrap(raw: bytes) -> bytes:
    p = protector()
    if p is None:
        raise DecryptError(
            "Файл зашифрован, а шифрование сейчас отключено или недоступно. "
            "Включите ENCRYPT_AT_REST и войдите под той же учётной записью Windows."
        )
    try:
        return p.unprotect(raw[len(MAGIC) :])
    except Exception as e:
        # Самая вероятная причина — файл зашифрован ДРУГОЙ учётной записью
        # (папку data/ перенесли) либо master-ключи DPAPI уничтожены
        # принудительным сбросом пароля. Пользователю нужна эта мысль, а не
        # трассировка.
        log.error("Не удалось расшифровать файл: %s", e)
        raise DecryptError(
            "Файл зашифрован для другой учётной записи Windows и не читается "
            "здесь. Обработайте документ заново."
        ) from e


def store(logical: Path, data: bytes) -> Path:
    """Кладёт данные на диск и возвращает ФАКТИЧЕСКИЙ путь (`.enc` или обычный).

    Отказывается писать, если шифрование обещано, но не работает: открытый
    договор на диске хуже, чем понятная ошибка на экране.
    """
    st = status()
    if st.broken:
        raise StorageUnprotected(
            f"Файл не сохранён: шифрование недоступно ({st.reason}). "
            "Документы не сохраняются открытым текстом."
        )
    # Приложение пишет только в свою рабочую папку. Проверка ДО mkdir: иначе
    # отклонённая запись успеет создать каталог в чужом месте.
    file_access.assert_writable(logical)
    logical.parent.mkdir(parents=True, exist_ok=True)

    p = protector()
    if p is None:
        logical.write_bytes(data)
        return logical

    enc = encrypted_path(logical)
    enc.write_bytes(_wrap(data, p))
    # Одноимённый открытый файл мог остаться с прошлых версий или с
    # отключённым шифрованием. Оставить его — значит зашифровать документ и
    # тут же оставить рядом его читаемую копию.
    logical.unlink(missing_ok=True)
    return enc


def encrypt_blob(data: bytes) -> bytes:
    """Шифрует данные для хранения НЕ в файле — например в колонке SQLite.

    Нужно результатам задач: там лежит разбор договора вместе с текстом
    документа, и класть его в app.db открытым значило бы сделать базу
    хранилищем договоров (в task_history текст намеренно не пишется).
    """
    p = protector()
    return _wrap(data, p) if p is not None else data


def decrypt_blob(raw: bytes) -> bytes:
    return _unwrap(raw) if raw.startswith(MAGIC) else raw


def load(logical: Path) -> bytes:
    """Читает файл, расшифровывая при необходимости."""
    src = stored_path(logical)
    if src is None:
        raise FileNotFoundError(logical)
    raw = src.read_bytes()
    # Решаем по содержимому, а не по имени: файл мог остаться открытым с
    # прошлых версий, и он должен продолжать читаться.
    return _unwrap(raw) if raw.startswith(MAGIC) else raw


@contextmanager
def plaintext(logical: Path) -> Iterator[Path]:
    """Открытая копия файла на время блока; после блока копии не остаётся.

    Нужно тем, кто умеет работать только с настоящим файлом на диске: OCR
    (Tesseract/poppler запускаются как процессы и получают путь), python-docx,
    pdfplumber. Имя файла сохраняется — по расширению выбирается парсер.
    """
    src = stored_path(logical)
    if src is None:
        raise FileNotFoundError(logical)
    raw = src.read_bytes()
    if not raw.startswith(MAGIC):
        # Файл и так открытый — копировать его во временный каталог значило бы
        # размножить открытый документ по диску.
        yield src
        return

    data = _unwrap(raw)
    tmpdir = Path(tempfile.mkdtemp(prefix="doc_", dir=_work_dir()))
    try:
        tmp = tmpdir / logical.name
        tmp.write_bytes(data)
        yield tmp
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@contextmanager
def encrypted_output(logical: Path) -> Iterator[Path]:
    """Путь, куда генератор пишет обычный файл; на выходе он оказывается зашифрован.

    Генераторы DOCX (python-docx) умеют только `doc.save(путь)`, и переучивать
    их на BytesIO ради шифрования — лишняя переделка рабочего кода. Поэтому им
    выдаётся временный путь, а шифрование происходит здесь.

    Если блок бросил исключение, результат НЕ сохраняется: недописанный DOCX в
    outputs выглядел бы как готовый файл.
    """
    st = status()
    if st.broken:
        raise StorageUnprotected(
            f"Файл не сохранён: шифрование недоступно ({st.reason}). "
            "Документы не сохраняются открытым текстом."
        )
    # Проверяем ЗДЕСЬ, а не только внутри store(): при выключенном шифровании
    # генератор пишет прямо по этому пути, и store() не вызывается вовсе.
    file_access.assert_writable(logical)
    logical.parent.mkdir(parents=True, exist_ok=True)
    if protector() is None:
        yield logical
        return

    tmpdir = Path(tempfile.mkdtemp(prefix="out_", dir=_work_dir()))
    try:
        tmp = tmpdir / logical.name
        yield tmp
        if tmp.exists():
            store(logical, tmp.read_bytes())
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _work_dir() -> Path:
    """data/tmp, а не системный %TEMP%: открытая копия договора не должна
    оставаться там, куда не дотягивается автоочистка."""
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    return config.WORK_DIR
