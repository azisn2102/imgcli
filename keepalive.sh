#!/usr/bin/env bash
# Keep the Google session alive so the Ubuntu box stays logged in.
# Usage: keepalive.sh {start|stop|status|log}
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="${IMGCLI_CONFIG_DIR:-$HOME/.config/imgcli}"
PIDFILE="$CONF/keepalive.pid"
LOG="${IMGCLI_KEEPALIVE_LOG:-$CONF/keepalive.log}"
INTERVAL="${IMGCLI_KEEPALIVE_MIN:-30}"   # minutes between refreshes
PROFILE="${IMGCLI_PROFILE:-$CONF/chrome-profile}"

mkdir -p "$CONF"

running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

start() {
  if running; then
    echo "keepalive already running (pid $(cat "$PIDFILE"))"
    return 0
  fi
  echo "starting keepalive (every ${INTERVAL} min, headless) ..."
  PYTHONUNBUFFERED=1 setsid nohup imgcli keepalive --headless --profile "$PROFILE" \
      --interval "$INTERVAL" >>"$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"
  sleep 2
  if running; then
    echo "started (pid $(cat "$PIDFILE")); log: $LOG"
  else
    echo "failed to start; see $LOG" >&2
    return 1
  fi
}

stop() {
  if running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
    rm -f "$PIDFILE"
    echo "stopped"
  else
    echo "not running"
  fi
}

status() {
  if running; then
    echo "running (pid $(cat "$PIDFILE"))"
    tail -n 3 "$LOG" 2>/dev/null
    return 0
  fi
  echo "not running"
  return 1
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  log) tail -n 40 "$LOG" 2>/dev/null ;;
  *) echo "usage: $0 {start|stop|status|log}" >&2; exit 2 ;;
esac
