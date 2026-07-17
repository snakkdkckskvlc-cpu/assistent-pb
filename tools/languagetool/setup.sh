#!/usr/bin/env bash
# Скачивает портативный JDK 17 (Eclipse Temurin) и релиз LanguageTool в
# этот каталог — один раз, идемпотентно (пропускает уже скачанное).
# Ничего не устанавливает в систему. ~430 МБ, нужен интернет один раз.
#
# macOS (arm64/x86_64) — проверено. Windows/Linux: URL для JDK нужно
# поменять на соответствующий (см. https://api.adoptium.net/q/swagger-ui/,
# os=windows|linux, arch=x64|aarch64) — по аналогии с install/windows/
# используем отдельный .ps1, который здесь пока не написан.
set -euo pipefail
cd "$(dirname "$0")"

JDK_DIR_GLOB="jdk-*"
LT_DIR_GLOB="LanguageTool-*"

if compgen -G "$JDK_DIR_GLOB" > /dev/null; then
  echo "JDK уже есть: $(compgen -G "$JDK_DIR_GLOB")"
else
  ARCH="$(uname -m)"
  case "$ARCH" in
    arm64) JDK_ARCH="aarch64" ;;
    x86_64) JDK_ARCH="x64" ;;
    *) echo "Неизвестная архитектура: $ARCH — скачайте JDK 17 вручную с https://adoptium.net/"; exit 1 ;;
  esac
  echo "Скачиваю портативный JDK 17 (Temurin, mac ${JDK_ARCH})…"
  curl -L -o jdk.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/17/ga/mac/${JDK_ARCH}/jdk/hotspot/normal/eclipse"
  tar -xzf jdk.tar.gz
  rm jdk.tar.gz
  echo "Готово: $(compgen -G "$JDK_DIR_GLOB")"
fi

if compgen -G "$LT_DIR_GLOB" > /dev/null; then
  echo "LanguageTool уже есть: $(compgen -G "$LT_DIR_GLOB")"
else
  echo "Скачиваю LanguageTool (~240 МБ)…"
  curl -L -o lt.zip "https://languagetool.org/download/LanguageTool-stable.zip"
  unzip -q lt.zip
  rm lt.zip
  echo "Готово: $(compgen -G "$LT_DIR_GLOB")"
fi

echo ""
echo "Установка завершена. Запуск: ./start.sh"
