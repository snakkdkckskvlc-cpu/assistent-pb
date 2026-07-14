#!/bin/bash
# Собирает macOS .app-бандл вокруг fire_safety_desktop.main.
# Результат: build/Ассистент ПБ.app — можно открыть двойным кликом.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APP_NAME="Ассистент ПБ"
APP="$BUILD/$APP_NAME.app"
BUNDLE_ID="ru.firesafety.assistant"
VERSION="0.2.0"

echo "== Building $APP_NAME.app =="

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Info.plist
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.business</string>
</dict>
</plist>
EOF

# Иконка
if [ ! -f "$BUILD/icons/AppIcon.icns" ]; then
    "$ROOT/venv/bin/python" "$ROOT/scripts/make_icons.py"
fi
cp "$BUILD/icons/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# Launcher-скрипт — с абсолютным путём к проекту, чтобы venv находился надёжно
cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
DIR="$ROOT"
LOG="\$HOME/Library/Logs/AssistentPB.log"
exec > >(tee -a "\$LOG") 2>&1
echo "=== \$(date) launching from \$DIR ==="
cd "\$DIR"
# LLM_MODEL можно менять — на боевом сервере поставить qwen2.5:14b-instruct-q4_K_M
export LLM_MODEL="\${LLM_MODEL:-qwen2.5:7b-instruct}"
export PYTHONPATH="\$DIR/apps/backend/src:\$DIR/packages/rag/src:\$DIR/apps/desktop/src"
exec "\$DIR/venv/bin/python" -m fire_safety_desktop.main
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

echo ""
echo "Готово: $APP"
echo "Запуск: open '$APP'"
