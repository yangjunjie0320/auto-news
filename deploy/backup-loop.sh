#!/bin/sh
# backup 容器的主进程：每隔一段时间跑一次 backup.sh。
#
# 与 rebuild-loop.sh 同样的形态：全量快照很便宜，不需要调度器，
# 出错由 compose 的 restart 策略兜底。
set -eu

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

while true; do
  if ! /deploy/backup.sh; then
    echo "[backup-loop] 备份失败，${INTERVAL}s 后重试" >&2
  fi
  sleep "$INTERVAL"
done
