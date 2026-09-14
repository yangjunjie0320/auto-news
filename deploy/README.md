# 部署

境外 Linux 服务器 + Docker Compose。三个常驻服务加一个跑完即退的种子注入。

```
seed     一次性：把 seed/digest 的历史数据注入 state 卷（幂等，已有数据就跳过）
scraper  常驻：每小时轮询三个新闻站 -> DeepSeek 分类+翻译 -> 写 state/digest/*.jsonl
builder  常驻循环：每小时跑一次 rebuild.sh，digest 没变就跳过
nginx    发静态文件，80 端口
```

只有 scraper 是真正干活的常驻进程。builder 绝大多数时候在 sleep，nginx 只发文件。
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
```

每个应打印最多 5 条真实新车资讯。空结果或解析异常 = 门槛未过。

汽车之家是 gb2312 编码，境外 CDN 可能返回不同页面，要肉眼核对标题内容是否合理，
不能只看有没有输出。

门槛不过的退路：抓取放回境内机器，只把网站部署到境外；或给抓取配代理。

### 2. 配置

```
cp config.example.yaml config.yaml
chmod 600 config.yaml        # --self-check 会拒绝任何带 group/other 权限位的配置
```

填 `deepseek_api_key`。这是唯一必需的密钥。

`config.yaml` 已在 `.gitignore` 里，不要提交。

### 3. 站点地址

RSS 的自引用链接需要真实域名：

```
export SITE_BASE_URL=https://your-domain.com
```

不设的话 build-data.py 会用占位域名 `https://autohot.example` 并告警。

## 启动

```
export SITE_BASE_URL=https://your-domain.com
docker compose up -d
```

首次启动顺序由 compose 保证：seed 跑完退出 → scraper 和 builder 才启动。

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

# HTTP
curl -sI http://localhost/          # 200
curl -sI http://localhost/daily     # 200，这是 nginx try_files 配错时最先坏的地方
curl -sI http://localhost/nope      # 404，不是 200
curl -s  http://localhost/rss.xml    | xmllint --noout -   # 合法
curl -s  http://localhost/rss.zh.xml | xmllint --noout -
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

**`current` 软链必须是相对路径。** builder 把卷挂在 `/data/www`，nginx 挂在
`/srv/www`。用绝对路径软链在 nginx 那边会悬空，表现是全站 404。

**rebuild.sh 靠 checksum 幂等**，所以可以放心每小时跑。任何一步失败都不动
`current`，线上保持上一个可用版本。

**digest 从不清理。** `state/digest/*.jsonl` 只追加。现在有网站读它了，
长期要定个保留策略，否则无限增长。

**没有单实例保护。** 代码里没有文件锁也没有 PID 文件。切换时务必先停掉
mac mini 上的 launchd agent，两边同时跑会写坏 state。

## 还没做的

- HTTPS：现在只监听 80。上线前套 caddy / traefik，或在 nginx 里配 certbot。
- 备份：`state` 卷里的 digest 是不可再生的历史数据，没有备份机制。
