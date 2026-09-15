#!/usr/bin/env bash
# 由 assets/icon/icon.svg 生成 .icns，并构建可双击的「Chat Analyzer.app」。
# 用法：scripts/make-launcher.sh ["/目标/路径/Chat Analyzer.app"]   默认装到 ~/Applications
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVG="$ROOT/assets/icon/icon.svg"
ICNS="$ROOT/assets/icon/ChatAnalyzer.icns"
APP="${1:-$HOME/Applications/Chat Analyzer.app}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log() { printf '\033[36m▸\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. icns（已存在且不比 SVG 旧则复用，避免依赖 SVG 渲染器） ──────────────
if [[ ! -f "$ICNS" || "$SVG" -nt "$ICNS" ]]; then
  PNG="$WORK/icon-1024.png"
  if command -v rsvg-convert >/dev/null; then
    rsvg-convert -w 1024 -h 1024 "$SVG" -o "$PNG"
  elif python3 -c 'import cairosvg' >/dev/null 2>&1; then
    python3 -c 'import cairosvg,sys; cairosvg.svg2png(url=sys.argv[1], write_to=sys.argv[2], output_width=1024, output_height=1024)' "$SVG" "$PNG"
  else
    qlmanage -t -s 1024 -o "$WORK" "$SVG" >/dev/null 2>&1 && mv "$WORK/icon.svg.png" "$PNG"
  fi
  [[ -s "$PNG" ]] || die "SVG 渲染失败，请装一个渲染器：brew install librsvg 或 pip install cairosvg"

  ICONSET="$WORK/icon.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s * 2))" "$((s * 2))" "$PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$ICNS"
  log "已生成 $ICNS"
fi

# ── 2. .app 壳：双击 → Terminal 里跑 start.command ─────────────────────────
chmod +x "$ROOT/scripts/start.command"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Chat Analyzer</string>
  <key>CFBundleDisplayName</key><string>聊天分析器</string>
  <key>CFBundleIdentifier</key><string>local.chat-analyzer.launcher</string>
  <key>CFBundleExecutable</key><string>ChatAnalyzer</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/ChatAnalyzer" <<SH
#!/bin/sh
# 由 scripts/make-launcher.sh 生成；项目移动位置后需重新执行该脚本。
exec open "$ROOT/scripts/start.command"
SH
chmod +x "$APP/Contents/MacOS/ChatAnalyzer"
touch "$APP"  # 刷新 Finder 图标缓存

log "启动器已就绪：$APP"
log "双击即可启动，也可拖到 Dock 或桌面（桌面用替身：右键 → 制作替身）"
