#!/bin/bash
# Start all voicebot services under supervisord.
# Usage: ./ops/start_supervisord.sh [start|stop|restart|status]
set -e

ACTION="${1:-start}"
CONF="$(dirname "$0")/supervisord.conf"

if ! command -v supervisord &>/dev/null; then
  echo "supervisord not found. Install with: pip install supervisor"
  exit 1
fi

case "$ACTION" in
  start)
    echo "Starting supervisord..."
    supervisord -c "$CONF"
    sleep 2
    supervisorctl -c "$CONF" status
    ;;
  stop)
    supervisorctl -c "$CONF" shutdown
    ;;
  restart)
    supervisorctl -c "$CONF" restart all
    ;;
  status)
    supervisorctl -c "$CONF" status
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]"
    exit 1
    ;;
esac
