#!/bin/sh
# builder 容器的主进程：每隔一段时间跑一次 rebuild.sh。
#
# 不用 cron 也不用调度器容器：rebuild.sh 靠 checksum 幂等，空转几乎零成本，
# 一个 sleep 循环就够，出错由 compose 的 restart 策略兜底。
set -eu

INTERVAL="${REBUILD_INTERVAL_SECONDS:-3600}"

# 首次启动先跑一次，不用等一个周期
while true; do
  if ! /deploy/rebuild.sh; then
    echo "[rebuild-loop] 构建失败，保持上一个版本，${INTERVAL}s 后重试" >&2
  fi
  sleep "$INTERVAL"
done
