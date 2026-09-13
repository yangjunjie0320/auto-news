#!/bin/sh
# 从 digest 重新构建站点，并原子切换到新版本。
#
# 幂等：digest 没变就直接退出，所以可以放心每小时跑一次。
# 任何一步失败都不会动 current，线上保持上一个可用版本。
set -eu

DIGEST_DIR="${DIGEST_DIR:-/data/state/digest}"
WWW_DIR="${WWW_DIR:-/data/www}"
APP_DIR="${APP_DIR:-/app}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

STAMP="$WWW_DIR/.last-digest-sha"

log() { echo "[rebuild] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

if [ ! -d "$DIGEST_DIR" ]; then
  log "digest 目录不存在，跳过: $DIGEST_DIR"
  exit 0
fi

# 只要内容有变就重建。用文件名+内容一起算，改名也能察觉。
sha=$(find "$DIGEST_DIR" -name '*.jsonl' -type f -exec sha256sum {} + \
      | sort | sha256sum | cut -d' ' -f1)

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$sha" ]; then
  log "digest 未变，跳过构建"
  exit 0
fi

log "digest 有变化，开始构建"
cd "$APP_DIR"

# SITE_BASE_URL 没设的话 build-data.py 会用占位域名并告警，构建不会失败
python3 -u scripts/build-data.py --source "$DIGEST_DIR" --out src/data/news.json
npm run build

stamp_name=$(date -u +%Y%m%dT%H%M%SZ)
release="$WWW_DIR/releases/$stamp_name"
mkdir -p "$release"
cp -a out/. "$release/"

# 软链必须是相对路径。builder 把卷挂在 /data/www，nginx 挂在 /srv/www，
# 绝对路径软链在 nginx 那边会悬空（实测过：全站 404）。
#
# 原子切换：先建临时软链再 rename。直接 ln -sfn 覆盖已存在的软链是
# unlink+symlink 两步，中间有一瞬间 current 不存在。
ln -sfn "releases/$stamp_name" "$WWW_DIR/.current.tmp"
mv -T "$WWW_DIR/.current.tmp" "$WWW_DIR/current"
log "已切换到 releases/$stamp_name"

echo "$sha" > "$STAMP"

# 旧版本只留最近几个
cd "$WWW_DIR/releases"
ls -1 | sort -r | tail -n +$((KEEP_RELEASES + 1)) | while read -r old; do
  log "清理旧版本 $old"
  rm -rf "$old"
done

log "完成"
