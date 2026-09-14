from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel


class Source(BaseModel):
    """一个抓取数据源（站点栏目）。key 作为去重命名空间，name 用于展示。"""

    key: str
    name: str
    enabled: bool = True
    # 抓取间隔。留空则用全局 poll_interval_seconds。
    # 微博用访客 cookie，抓太勤会被限流，所以单独放慢到每天一次。
    interval_seconds: int | None = None
    # 落进归档的来源类型，供网站与 RSS 区分新闻站文章和微博观点
    kind: str = "web"


@dataclass(frozen=True)
class PushResult:
    """推送处理结果，区分真实发送、业务丢弃与失败。"""

    handled: bool
    pushed: bool = False
    dropped: bool = False

    @classmethod
    def sent(cls) -> PushResult:
        return cls(handled=True, pushed=True)

    @classmethod
    def discarded(cls) -> PushResult:
        return cls(handled=True, dropped=True)

    @classmethod
    def processed(cls) -> PushResult:
        return cls(handled=True)

    @classmethod
    def failed(cls) -> PushResult:
        return cls(handled=False)


class Post(BaseModel):
    """一篇抓取到的文章。

    字段名沿用自 weibo-monitor（本项目由它迁移而来，见 docs/迁移设计.md），
    语义已重映射到「文章」：

    - uid          -> 数据源 key（去重命名空间），如 "autohome-newbrand"
    - screen_name  -> 数据源展示名，如 "汽车之家·上市新车"
    - mid          -> 文章在本数据源内的稳定唯一 ID（去重键）
    - url          -> 文章原文链接
    - created_at   -> 文章发布/上市日期（tz-aware，统一 UTC；仅有日期时取当日 00:00）
    - title        -> 文章标题
    - text_plain   -> 供分类与兼容旧投递逻辑的文本（标题 + 正文）
    - full_text    -> 详情页提取的完整正文（不含标题）
    - image_urls   -> 封面图（0 或多张）
    """

    uid: str
    screen_name: str = ""
    mid: str
    url: str
    created_at: dt.datetime
    created_at_is_precise: bool = False
    title: str = ""
    text_plain: str = ""
    full_text: str = ""
    image_urls: list[str] = []
    # 来源类型，随抓取器设定，透到归档供网站与 RSS 区分新闻与微博观点
    kind: str = "web"
