import datetime as dt

from src.models import Post

CST = dt.timezone(dt.timedelta(hours=8))


def make_post(**overrides) -> Post:
    """构造一条测试用文章。

    字段名沿用自 weibo-monitor 时期的 Post 模型（uid=数据源 key、
    screen_name=数据源展示名、mid=文章 id），语义见 docs/迁移设计.md。
    """
    base = {
        "uid": "yiche-u61014816",
        "screen_name": "易车·易车原创",
        "mid": "yiche-1",
        "bid": "Babc",
        "url": "https://news.yiche.com/1.html",
        "created_at": dt.datetime(2026, 7, 1, 12, 30, tzinfo=CST),
        "text_plain": "今天试驾了一台新车",
        "source": "易车",
        "reposts_count": 12,
        "comments_count": 34,
        "attitudes_count": 56789,
    }
    base.update(overrides)
    return Post(**base)
