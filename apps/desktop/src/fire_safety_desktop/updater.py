"""Автообновление приложения с GitHub (для установок не у разработчика).

Работает через git, а не полную перекачку архива — так обновление тянет
только реально изменившиеся файлы, а не гигабайты poppler/tesseract-
инсталляторов, которые тоже лежат в репозитории.

Безопасность: обновляется ТОЛЬКО "чистая" установка — ветка main без
незакоммиченных изменений. Если это похоже на рабочую копию разработчика
(другая ветка, есть правки) — тихо ничего не делаем. Любая ошибка на любом
шаге — тоже тихо ничего не делаем: обновление не должно быть причиной,
по которой приложение не запустилось.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

GITHUB_REMOTE_BRANCH = "main"
FETCH_TIMEOUT_SEC = 10
APPLY_TIMEOUT_SEC = 600


def _run(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(root: Path, *args: str, timeout: float = FETCH_TIMEOUT_SEC) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd=root, timeout=timeout)


def _is_safe_to_update(root: Path) -> bool:
    """main, без незакоммиченных изменений — иначе это, вероятно, рабочая
    копия разработчика, и её трогать нельзя."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.returncode != 0 or branch.stdout.strip() != GITHUB_REMOTE_BRANCH:
        return False

    status = _git(root, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        return False

    return True


def _reinstall_dependencies(root: Path) -> bool:
    req_file = root / "requirements-runtime.txt"
    if req_file.exists():
        r = _run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet", "--prefer-binary"],
            cwd=root,
            timeout=APPLY_TIMEOUT_SEC,
        )
        if r.returncode != 0:
            log.warning("update: dependency reinstall failed: %s", r.stderr)
            return False

    r = _run(
        [
            sys.executable, "-m", "pip", "install", "--no-deps", "--quiet",
            "-e", str(root / "apps" / "backend"),
            "-e", str(root / "apps" / "desktop"),
            "-e", str(root / "packages" / "rag"),
        ],
        cwd=root,
        timeout=APPLY_TIMEOUT_SEC,
    )
    if r.returncode != 0:
        log.warning("update: editable install failed: %s", r.stderr)
        return False
    return True


def check_and_apply_update(root: Path) -> bool:
    """Возвращает True, если обновление применено (вызывающий код должен
    перезапустить приложение, чтобы подхватить новый код)."""
    if os.environ.get("ASSISTENT_PB_DISABLE_AUTOUPDATE"):
        return False

    if shutil.which("git") is None:
        return False

    if not (root / ".git").exists():
        return False

    try:
        if not _is_safe_to_update(root):
            return False

        fetch = _git(root, "fetch", "origin", GITHUB_REMOTE_BRANCH, "--quiet")
        if fetch.returncode != 0:
            return False  # обычно просто нет сети — это нормально, работаем офлайн

        local = _git(root, "rev-parse", "HEAD")
        remote = _git(root, "rev-parse", f"origin/{GITHUB_REMOTE_BRANCH}")
        if local.returncode != 0 or remote.returncode != 0:
            return False
        if local.stdout.strip() == remote.stdout.strip():
            return False  # уже последняя версия

        reset = _git(root, "reset", "--hard", f"origin/{GITHUB_REMOTE_BRANCH}", "--quiet", timeout=30)
        if reset.returncode != 0:
            log.warning("update: git reset failed: %s", reset.stderr)
            return False

        if not _reinstall_dependencies(root):
            # Код уже обновлён, но зависимости — нет. Всё равно перезапускаем:
            # main.py при следующем старте сам покажет понятную ошибку
            # ImportError, если чего-то не хватает, вместо тихого краша.
            log.warning("update: applied code update but dependency reinstall failed")

        return True
    except Exception:
        log.exception("update: check/apply failed")
        return False
