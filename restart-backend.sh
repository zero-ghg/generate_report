#!/usr/bin/env bash
# Restart this project's Django development server on 127.0.0.1:8000.
# Default mode keeps the server attached to this terminal so startup failures
# remain visible instead of being silently swallowed by a detached process.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="127.0.0.1"
PORT="8000"
LOG_FILE="${SCRIPT_DIR}/.django-dev-server.log"
PID_FILE="${SCRIPT_DIR}/.django-dev-server.pid"
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
BACKGROUND=false

if [[ "${1:-}" == "--background" ]]; then
  BACKGROUND=true
elif [[ -n "${1:-}" ]]; then
  echo "用法：$0 [--background]" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到后端虚拟环境：$PYTHON_BIN" >&2
  exit 1
fi

# Only stop a process if it is clearly this project's Django dev server.
while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$command_line" == *"manage.py runserver"* && ( "$command_line" == *"${SCRIPT_DIR}/.venv/bin/python"* || "$command_line" == *".venv/bin/python"* ) ]]; then
    echo "停止旧后台进程 PID ${pid}"
    kill "$pid"
  elif [[ -n "$command_line" ]]; then
    echo "端口 ${PORT} 正被其他进程占用，未自动停止：${command_line}" >&2
    exit 1
  fi
done < <(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)

# Wait briefly for the previous listener to release the port.
for _ in {1..20}; do
  if ! lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "后台进程未能在限定时间内退出，端口 ${PORT} 仍被占用。" >&2
  exit 1
fi

cd "$SCRIPT_DIR"
if [[ "$BACKGROUND" == true ]]; then
  nohup "$PYTHON_BIN" manage.py runserver "${HOST}:${PORT}" --noreload >>"$LOG_FILE" 2>&1 &
  server_pid=$!
  echo "$server_pid" > "$PID_FILE"
  sleep 1
  if kill -0 "$server_pid" 2>/dev/null && lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "后台已启动：http://${HOST}:${PORT}（PID ${server_pid}）"
    echo "日志：${LOG_FILE}"
    exit 0
  fi
  echo "后台启动失败，请查看日志：${LOG_FILE}" >&2
  exit 1
fi

echo "后台将在当前终端运行：http://${HOST}:${PORT}"
echo "按 Ctrl+C 可停止服务。若要后台运行，请使用：$0 --background"
exec "$PYTHON_BIN" manage.py runserver "${HOST}:${PORT}" --noreload
