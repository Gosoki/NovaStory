#!/usr/bin/env bash
# NovaStory 启动脚本 —— 不依赖 systemd,普通后台进程。
#
# 用法:
#   scripts/start.sh            启动(已在跑就什么都不做)
#   scripts/start.sh status     看状态
#   scripts/start.sh stop       停止
#   scripts/start.sh restart    重启
#
# 开机自启(可选,不用装 systemd 单元):
#   crontab -e  加一行 →  @reboot cd /root/coding/local/Claude/NovaStory && scripts/start.sh
#
# 走 scripts/serve.py 而不是 streamlit 本体:它让静态资源直接发预压缩产物。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
PORT="${NOVASTORY_PORT:-8501}"
ADDR="${NOVASTORY_ADDR:-0.0.0.0}"   # 反代在另一台机器(10.0.0.1)上,不能绑 127.0.0.1
PIDFILE="$ROOT/data/novastory.pid"
LOG="$ROOT/data/serve.log"
PY="$ROOT/.venv/bin/python"

running_pid() {
  # 只认自己 pidfile 里那个、且确实是本项目 serve.py 的进程
  [ -f "$PIDFILE" ] || return 1
  local p; p="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$p" ] && [ -d "/proc/$p" ] && tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q 'serve.py' && echo "$p"
}

port_owner() {
  ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p" *$" || $4 ~ p"$" {print $NF}' | head -1
}

health() {
  curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/_stcore/health" 2>/dev/null
}

case "${1:-start}" in
  status)
    pid="$(running_pid)" && echo "运行中 pid=$pid" || echo "未运行(至少不是本脚本起的)"
    echo "端口 $PORT 占用者: $(port_owner || echo 无)"
    echo "健康检查: $(health)"
    exit 0 ;;

  stop)
    pid="$(running_pid)"
    if [ -n "${pid:-}" ]; then
      kill "$pid" && echo "已停止 pid=$pid"
    else
      echo "本脚本没在管任何进程。若服务是 systemd 起的,用: systemctl stop novastory"
    fi
    exit 0 ;;

  restart)
    "$0" stop; sleep 2; exec "$0" start ;;
esac

# ---------- start ----------
if pid="$(running_pid)"; then
  echo "已经在跑了 pid=$pid (健康检查 $(health)),什么都不做。"
  exit 0
fi

# 端口被别人占着:多半是 systemd 那个单元还活着。不擅自去杀别人的进程。
if owner="$(port_owner)" && [ -n "$owner" ]; then
  echo "✗ 端口 $PORT 已被占用: $owner"
  if systemctl is-active --quiet novastory.service 2>/dev/null; then
    echo "  它是 systemd 的 novastory.service。要改用本脚本的话先:"
    echo "    systemctl stop novastory && systemctl disable novastory 2>/dev/null"
    echo "  想继续用 systemd 就不必跑这个脚本,直接 systemctl restart novastory。"
  fi
  exit 1
fi

mkdir -p "$ROOT/data"
# 日志超过 20 MB 就转存一份,避免长期采数把盘写满(采数期实测每人约 30 行,涨得很慢)
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 20971520 ]; then
  mv "$LOG" "$LOG.$(date +%Y%m%d_%H%M%S)"
fi

# 静态资源预压缩(幂等;产物已是最新时几乎瞬间返回)
"$PY" "$ROOT/scripts/precompress_static.py" --quiet 2>>"$LOG"

nohup "$PY" "$ROOT/scripts/serve.py" run app.py \
  --server.port "$PORT" --server.address "$ADDR" --server.headless true \
  --server.runOnSave false --server.fileWatcherType none \
  >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"

# 等它真的起来再报成功 —— 只打印 pid 不算数
for _ in $(seq 1 40); do
  [ "$(health)" = "200" ] && { echo "✓ 已启动 pid=$(cat "$PIDFILE")  端口 $PORT  日志 $LOG"; exit 0; }
  sleep 1
done
echo "✗ 起了进程但 40 秒内健康检查没通过,看日志: tail -50 $LOG"
exit 1
