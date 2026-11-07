#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "$STATE_FILE"

case "${1:-}" in
  start) aws ec2 start-instances --instance-ids "$SERVER_ID" "$GPU_ID" >/dev/null && echo "Starting both nodes" ;;
  stop) aws ec2 stop-instances --instance-ids "$SERVER_ID" "$GPU_ID" >/dev/null && echo "Stopping both nodes" ;;
  stop-gpu) aws ec2 stop-instances --instance-ids "$GPU_ID" >/dev/null && echo "Stopping the GPU node" ;;
  *) echo "usage: $0 start|stop|stop-gpu" >&2; exit 1 ;;
esac
