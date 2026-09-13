# auto-news-monitor

汽车资讯监控：每小时轮询易车/新浪汽车/汽车之家三个数据源，提取新文章全文，
经 DeepSeek 分类并生成要点摘要后推送到飞书群；卡片以数据源为标签、默认折叠完整文案，并支持
一键转发与多维表格归档。

由 weibo-monitor 迁移而来：投递层（飞书推送、分类、转发、归档）整体复用，
抓取层替换为三个免登录 SSR 站点的解析器（`src/sources/`）。

## 用法
```
uv sync
cp config.example.yaml config.yaml   # 填 app_id/app_secret/chat_id/deepseek_api_key
uv run python -u main.py --self-check
uv run python -u main.py --probe yiche-u61014816   # 单源抓取自检
uv run python -u main.py --once --dry-run           # 全链路演练，不发送
uv run python -u main.py                             # 常驻，每小时轮询
```

## 数据源
`sources.yaml` 按 key 启用。当前支持：
- `yiche-u61014816` 易车原创个人主页
- `sina-newcar-news` 新浪汽车新车资讯流（正文）；旧车型日历 `sina-newcar` 默认关闭
- `autohome-newbrand` 汽车之家上市新车
