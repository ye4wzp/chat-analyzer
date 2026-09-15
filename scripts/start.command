#!/usr/bin/env bash
# 双击启动 Chat Analyzer：备好依赖 → 起后端 → 开浏览器。Ctrl-C 或关闭窗口即停止。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST=127.0.0.1
PORT="${CA_PORT:-8000}"
VENV="$ROOT/backend/.venv"
URL="http://$HOST:$PORT"

log()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; read -r -n 1 -s -p "按任意键关闭…"; exit 1; }
port_open() { nc -z "$HOST" "$PORT" >/dev/null 2>&1; }
# 端口开着不代表是自家后端，用 /openapi.json 的标题做指纹（不受 API Token 拦截）。
is_ours()   { curl -fsS --max-time 2 "$URL/openapi.json" 2>/dev/null | grep -q '"title":"Chat Analyzer"'; }

if port_open; then
  if is_ours; then
    log "服务已在 $URL 运行"
    open "$URL"
    exit 0
  fi
  warn "端口 $PORT 被其他进程占用："
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sed 1d | awk '{printf "    %s  PID %s\n", $1, $2}'
  die "请先结束该进程，或换端口启动：CA_PORT=8001 \"$ROOT/scripts/start.command\""
fi

command -v python3 >/dev/null || die "未找到 python3，请先安装 Python 3.12+"

if [[ ! -x "$VENV/bin/python" ]]; then
  log "首次运行：创建虚拟环境"
  python3 -m venv "$VENV" || die "创建虚拟环境失败"
fi

if ! "$VENV/bin/python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  log "安装后端依赖"
  "$VENV/bin/pip" install -q -e "$ROOT/backend" || die "后端依赖安装失败"
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  command -v npm >/dev/null || die "前端未构建且未找到 npm，请手动执行：cd frontend && npm install && npm run build"
  log "首次运行：构建前端（约 1 分钟）"
  (cd "$ROOT/frontend" && npm install --silent && npm run build) || die "前端构建失败"
fi

# 后端真正就绪（不只是端口 bind）后再开浏览器，最多等 30 秒
(
  for _ in $(seq 60); do
    if is_ours; then open "$URL"; exit 0; fi
    sleep 0.5
  done
) &

log "启动后端 $URL —— Ctrl-C 或关闭窗口即停止"
cd "$ROOT/backend"
exec "$VENV/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
