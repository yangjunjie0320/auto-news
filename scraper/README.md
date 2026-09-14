# scraper

每小时轮询易车/新浪汽车/汽车之家三个数据源，提取新文章全文，经 DeepSeek 分类、
生成中文要点摘要并翻成英文，落成 `state/digest/YYYY-MM-DD.jsonl`。

这些 jsonl 是 `../web` 的全部输入——网站和两个 RSS feed 都由它生成。
本项目没有别的产出。

由 weibo-monitor 迁移而来（见 `../docs/迁移设计.md`），抓取层是三个免登录 SSR
站点的解析器（`src/sources/`）。早期版本还有一条飞书投递链路（卡片推送、转发、
多维表格归档、每日日报），已整体移除。

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
uv run pytest        # 91 个，应全绿
uv run ruff check .
```

## 数据源

`sources.yaml` 按 key 启用。当前支持：

- `yiche-u61014816` 易车原创个人主页
- `sina-newcar-news` 新浪汽车新车资讯流（正文）；旧车型日历 `sina-newcar` 默认关闭
- `autohome-newbrand` 汽车之家上市新车

## 归档格式

一条文章一行 JSON（`src/archive.py` 的 `DigestRecord`）。中英并存是刻意的：

- `title` / `summary` / `label` / `source` —— 中文，供中文 RSS 和网站的热度打分
  （`build-data.py` 的 `compute_heat` 匹配中文关键词）
- `title_en` / `summary_en` —— 英文，供英文站与英文 RSS；翻译失败时为空串，
  网站会回退中文并标 `translated: false`

**绝不能把中文字段替换成英文**，两边都会坏。

## 待办

- `Classification.promo`（商家导购/软文判定）目前没有消费者：既不进归档，
  也不影响是否丢弃。要么接进 `DigestRecord` 让网站能过滤广告，要么从 prompt
  里删掉省 token。
- `state/digest/*.jsonl` 只追加、从不清理。现在有网站读它了，需要定个保留策略。
