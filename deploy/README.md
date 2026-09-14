# 部署

境外 Linux 服务器 + Docker Compose。三个常驻服务加一个跑完即退的种子注入。

```
seed     一次性：把 seed/digest 的历史数据注入 state 卷（幂等，已有数据就跳过）
scraper  常驻：轮询新闻站(每小时)与微博(每天) -> DeepSeek 分类+翻译 -> 写 digest
builder  常驻循环：每小时跑一次 rebuild.sh，digest 没变就跳过
backup   常驻循环：每天把 digest 全量快照到 ./backups/，保留 30 份
caddy    发静态文件并终止 TLS，自动申请 Let's Encrypt 证书
```

只有 scraper 是真正干活的常驻进程。builder 绝大多数时候在 sleep，caddy 只发文件。
**服务器上不跑 Node 服务**——网站是构建时全量预渲染的静态文件。

## 上线前必须做的事

### 1. 验证境外能抓到这三个站（硬门槛）

抓取层是静默降级的：抓不到不报错，只是没数据。不先验证的话，部署完会得到一个
永远停在种子数据上的僵尸站。

```
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --probe yiche-u61014816'
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --probe sina-newcar-news'
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --probe autohome-newbrand'
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --probe weibo-pool'
```

前三个应各打印最多 5 条真实新车资讯。空结果或解析异常 = 门槛未过。

汽车之家是 gb2312 编码，境外 CDN 可能返回不同页面，要肉眼核对标题内容是否合理，
不能只看有没有输出。

**微博这一条单独说**：它用的是访客 cookie，对境外 IP 的限流通常比新闻站严得多。
`--probe weibo-pool` 会遍历 34 个账号（带随机延迟，约 3 分钟）。看日志里的
`weibo timeline failed` 有多少条：
- 全部失败 → 境外抓不动微博，在 `sources.yaml` 里把 `weibo-pool` 关掉即可，
  不影响新闻站那条线
- 零星失败 → 正常，单账号失败不会让整个源失败
- 出现 `RateLimitedError`/`challenge response` → IP 级限流，重试无益，
  要么关掉微博源，要么给它配代理

门槛不过的退路：抓取放回境内机器，只把网站部署到境外；或给抓取配代理。

### 2. 配置

```
cp config.example.yaml config.yaml
chmod 600 config.yaml        # --self-check 会拒绝任何带 group/other 权限位的配置
```

填 `deepseek_api_key`。这是唯一必需的密钥。

`config.yaml` 已在 `.gitignore` 里，不要提交。

### 3. 域名与 HTTPS

两个环境变量，都要设：

```
export SITE_ADDRESS=your-domain.com            # Caddy 用它自动申请证书
export SITE_BASE_URL=https://your-domain.com   # RSS 自引用链接
```

`SITE_ADDRESS` 填真实域名时，Caddy 会自动申请并续期 Let's Encrypt 证书、
自动把 HTTP 跳转到 HTTPS。前提是**域名已解析到这台服务器，且 80/443 都开放**
（ACME 的 HTTP-01 挑战走 80 端口）。不设时默认 `:80`，纯 HTTP，只适合本地验证。

`SITE_BASE_URL` 不设的话 build-data.py 会用占位域名 `https://autohot.example` 并告警。

证书存在 `caddy_data` 卷里，**不要删这个卷**——重建会重新签发，很快会撞上
Let's Encrypt 的速率限制。

## 启动

```
export SITE_ADDRESS=your-domain.com
export SITE_BASE_URL=https://your-domain.com
docker compose up -d
```

首次启动顺序由 compose 保证：seed 跑完退出 → scraper 和 builder 才启动。
首次签发证书要几十秒，`docker compose logs -f caddy` 能看到 ACME 过程。

## 验证

```
# 离线自检（配置、路径可写性、密钥）
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --self-check'

# 跑一轮抓取，看 digest 有没有新增带英文的行
docker compose run --rm --entrypoint sh scraper -c \
  'python -u main.py --config /config/config.yaml --once'
docker compose run --rm --entrypoint sh seed -c \
  'tail -1 /data/state/digest/$(date -u -d "+8 hours" +%F).jsonl' | python3 -m json.tool

# 立刻触发一次构建，不等循环
docker compose exec builder /deploy/rebuild.sh

# HTTP（本地）/ HTTPS（配了域名后把 localhost 换成域名）
curl -sI http://localhost/          # 200
curl -sI http://localhost/daily     # 200，这是 try_files 配错时最先坏的地方
curl -sI http://localhost/nope      # 404，不是 200
curl -s  http://localhost/rss.xml    | xmllint --noout -   # 合法
curl -s  http://localhost/rss.zh.xml | xmllint --noout -

# 配了域名后额外验证 HTTPS 与跳转
curl -sI http://your-domain.com/     # 308 跳 https
curl -sI https://your-domain.com/    # 200
```

检查 `--once` 产出的那一行：`title_en` / `summary_en` 非空、里面没有汉字、
金额是 `¥380,000` 形式、数量是 `12,000 units` 且**不带 `¥``。
最后这条违反了不会报错，只会让前端货币换算静默失效。

## 日常操作

```
docker compose logs -f scraper          # 抓取日志
docker compose logs -f builder          # 构建日志
docker compose exec builder /deploy/rebuild.sh   # 手动触发构建
docker compose ps
```

改了代码后：

```
git pull && docker compose up -d --build
```

## 设计上要知道的几件事

**scraper 和 builder 必须同一个 UID。** `atomic_json.py` 会把 state 目录 chmod 成
`0700`，UID 不同 builder 就读不到 digest。两个 Dockerfile 都建了 `10001:10001`
并预先 chown 好挂载点——命名卷首次创建时会继承镜像里该路径的属主，不这么做的话
卷属 root，非 root 容器一写就 Permission denied。

**`current` 软链必须是相对路径。** builder 把卷挂在 `/data/www`，caddy 挂在
`/srv/www`。用绝对路径软链在 caddy 那边会悬空，表现是全站 404。

**rebuild.sh 靠 checksum 幂等**，所以可以放心每小时跑。任何一步失败都不动
`current`，线上保持上一个可用版本。

**digest 从不清理。** `state/digest/*.jsonl` 只追加。现在有网站读它了，
长期要定个保留策略，否则无限增长。

**`SITE_ADDRESS` 的默认值写在 compose 里而不是 Caddyfile 里。** Caddy 的
`{$VAR:default}` 在变量「设了但为空」时不会回落到默认值，会让 Caddyfile 行首
剩一个 `{` 被当成全局配置块，启动直接失败。

**没有单实例保护。** 代码里没有文件锁也没有 PID 文件。两个实例同时跑会写坏 state。

## 备份与还原

`backup` 服务每天把 `state/digest`（和 `seen.json`）全量打包到宿主机的
`./backups/state-<UTC时间戳>.tar.gz`，保留 30 份。数据是纯文本且只追加，
压缩后极小（76 条 ≈ 13KB），所以直接全量快照，不做增量。

**这只是本机快照，不是异地备份。** 服务器整台没了备份也跟着没。
把 `./backups/` 同步到别处（rclone、S3、另一台机器的 rsync）是必须另外做的一步。

手动触发一次：

```
docker compose exec backup /deploy/backup.sh
```

还原：

```
docker compose down                     # 先停掉，避免写入冲突
mkdir -p /tmp/restore && tar -xzf backups/state-<时间戳>.tar.gz -C /tmp/restore
docker compose run --rm -v /tmp/restore:/restore:ro --entrypoint sh seed -c \
  'rm -rf /data/state/digest && cp -a /restore/digest /data/state/ \
   && [ -f /restore/seen.json ] && cp /restore/seen.json /data/state/ || true'
docker compose up -d
docker compose exec builder /deploy/rebuild.sh   # 立刻用还原的数据重建
```

还原后务必核对条数对得上：

```
docker compose run --rm --entrypoint sh seed -c 'cat /data/state/digest/*.jsonl | wc -l'
```

## 还没做的

- 异地备份（见上）。
