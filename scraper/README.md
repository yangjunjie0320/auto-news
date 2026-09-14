# scraper

轮询新闻站与微博，经 DeepSeek 分类、生成中文要点摘要并翻成英文，
落成 `state/digest/YYYY-MM-DD.jsonl`。

这些 jsonl 是 `../web` 的全部输入——网站和两个 RSS feed 都由它生成。
本项目没有别的产出。

由 weibo-monitor 迁移而来（见 `../docs/迁移设计.md`）。早期版本还有一条飞书投递链路
（卡片推送、转发、多维表格归档、每日日报），已整体移除。

## 用法

```
uv sync --extra dev                  # 裸 uv sync 不装 pytest/respx/ruff
cp config.example.yaml config.yaml   # 只需填 deepseek_api_key
uv run python -u main.py --self-check                # 离线校验，不需要网络
uv run python -u main.py --probe yiche-u61014816     # 单源抓取自检
uv run python -u main.py --once --dry-run            # 全链路演练，不写归档
uv run python -u main.py                             # 常驻，每小时轮询
```

测试不需要网络和任何凭证：HTTP 用 respx 打桩，解析器用保存的 HTML fixture。

```
uv run pytest        # 118 个，应全绿
uv run ruff check .
```

## 数据源

`sources.yaml` 按 key 启用。两类源性质不同：**新闻站给官方事实，微博给增量观点**
（实车体验、渠道见闻、质疑、分析），落进同一份归档，靠 `kind` 字段区分。

新闻站（免登录 SSR，每小时抓）：

- `yiche-u61014816` 易车原创个人主页
- `sina-newcar-news` 新浪汽车新车资讯流（正文）；旧车型日历 `sina-newcar` 默认关闭
- `autohome-newbrand` 汽车之家上市新车

微博（访客 cookie，**每天抓一次**）：

- `weibo-pool` 人工筛选的 34 个博主账号池（`pool.yaml`），抓各自时间线第一页
- `weibo-search` 固定关键词搜索，**默认关闭**。关键词必须是专有名词（品牌名、车型名）：
  实测「新能源汽车」这类泛词搜回来 15 条里只有 2～3 条相关，其余是广告和科普。
  搜索结果还会按「互动 ≥5 **或** 粉丝 ≥10 万」过滤营销号——用「或」是因为
  刚官宣的事件互动量普遍为 0。

**微博为什么不跟着每小时抓**：用的是访客 cookie，34 个账号 × 24 次/天 = 816 次请求，
很容易被限流。`sources.yaml` 里给微博源配了 `interval_seconds: 86400`，
monitor 按 `state/seen.json` 里记的 `last_poll` 判断是否到点，没到就跳过。
微博是观点类内容，时效性要求本来也不高。

## 归档格式

一条文章一行 JSON（`src/archive.py` 的 `DigestRecord`）。中英并存是刻意的：

- `title` / `summary` / `label` / `source` —— 中文，供中文 RSS 和网站的热度打分
  （`build-data.py` 的 `compute_heat` 匹配中文关键词）
- `title_en` / `summary_en` —— 英文，供英文站与英文 RSS；翻译失败时为空串，
  网站会回退中文并标 `translated: false`
- `kind` —— `web`（新闻站）或 `weibo`（微博观点），网站据此提供来源筛选。
  旧数据没有这个字段，消费端按 `web` 回落

**绝不能把中文字段替换成英文**，两边都会坏。

## 待办

- `Classification.promo`（商家导购/软文判定）目前没有消费者：既不进归档，
  也不影响是否丢弃。要么接进 `DigestRecord` 让网站能过滤广告，要么从 prompt
  里删掉省 token。
- `state/digest/*.jsonl` 只追加、从不清理。现在有网站读它了，需要定个保留策略。
