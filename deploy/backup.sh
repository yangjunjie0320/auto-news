#!/bin/sh
# 把 state 里不可再生的数据打包到宿主机挂载的 ./backups/。
#
# 要保护的只有 digest：它是网站与 RSS 的全部输入，只追加、抓不回来。
# seen.json 一并带上（丢了会漏一轮新条目），health.json 可再生，不管。
#
# 数据是纯文本且只追加，压缩后极小（当前 76 条 = 152KB，压完约 40KB），
# 所以直接全量快照，不做增量——增量的复杂度换不来任何东西。
set -eu

STATE_DIR="${STATE_DIR:-/data/state}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${BACKUP_KEEP:-30}"

log() { echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

if [ ! -d "$STATE_DIR/digest" ]; then
  log "没有 digest 目录，跳过: $STATE_DIR/digest"
  exit 0
fi

mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIR/state-$stamp.tar.gz"

# 先写临时文件再改名：备份写到一半被中断，不该留下一个看着像成功的坏包
tmp="$BACKUP_DIR/.state-$stamp.tar.gz.tmp"
tar -czf "$tmp" -C "$STATE_DIR" digest $([ -f "$STATE_DIR/seen.json" ] && echo seen.json)
mv "$tmp" "$target"

lines=$(cat "$STATE_DIR"/digest/*.jsonl 2>/dev/null | wc -l | tr -d ' ')
log "已备份 $lines 条记录 -> $(basename "$target") ($(du -h "$target" | cut -f1))"

# 过期的删掉
find "$BACKUP_DIR" -name 'state-*.tar.gz' -type f -mtime "+$KEEP_DAYS" -print -delete \
  | while read -r old; do log "清理过期备份 $(basename "$old")"; done

# 半途失败留下的临时文件也清掉
find "$BACKUP_DIR" -name '.state-*.tar.gz.tmp' -type f -mtime +1 -delete 2>/dev/null || true
