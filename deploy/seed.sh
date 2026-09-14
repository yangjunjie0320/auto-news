#!/bin/sh
# 首次启动时 state 卷是空的，把仓库里的历史 digest 拷进去，
# 站点第一天就有内容，不必等抓取积累。
#
# 幂等：digest 目录已有内容就什么都不做。跑完即退出，属正常状态。
set -eu

DIGEST_DIR="${DIGEST_DIR:-/data/state/digest}"
SEED_DIR="${SEED_DIR:-/seed/digest}"

mkdir -p "$DIGEST_DIR"

if [ -n "$(ls -A "$DIGEST_DIR" 2>/dev/null)" ]; then
  echo "[seed] digest 已有数据，跳过"
  exit 0
fi

cp "$SEED_DIR"/*.jsonl "$DIGEST_DIR"/
echo "[seed] 注入 $(ls -1 "$DIGEST_DIR" | wc -l) 个文件"
