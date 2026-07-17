#!/usr/bin/env bash
# Запускает LanguageTool сервер локально (127.0.0.1:8081) на вендоренном
# JDK — ничего в системе трогать не нужно. Держать запущенным рядом с
# Ollama, пока работает бэкенд. Остановка — Ctrl+C.
#
# Подробности: apps/backend/src/fire_safety_backend/resources/... не
# трогает; сервер вызывается по HTTP из
# infrastructure/languagetool.py. См. references/languagetool-master/
# README_reference.md — почему это отдельный процесс, а не встроенная
# Java-библиотека (LGPL, sidecar снимает вопрос линковки).
set -euo pipefail
cd "$(dirname "$0")"

if ! compgen -G "jdk-*" > /dev/null || ! compgen -G "LanguageTool-*" > /dev/null; then
  echo "JDK/LanguageTool не найдены — сначала запустите ./setup.sh" >&2
  exit 1
fi

JDK_DIR="$(compgen -G "jdk-*" | head -1)"
LT_DIR="$(compgen -G "LanguageTool-*" | head -1)"
JAVA_BIN="$(pwd)/${JDK_DIR}/Contents/Home/bin/java"
if [ ! -x "$JAVA_BIN" ]; then
  # Не-macOS вендоренный JDK может иметь другую структуру (без Contents/Home).
  JAVA_BIN="$(pwd)/${JDK_DIR}/bin/java"
fi

PORT="${LT_PORT:-8081}"

echo "LanguageTool сервер: http://127.0.0.1:${PORT} (словарь: dict/spelling_global.txt)"
exec "$JAVA_BIN" \
  -cp "${LT_DIR}/languagetool-server.jar:dict" \
  org.languagetool.server.HTTPServer \
  --port "$PORT"
