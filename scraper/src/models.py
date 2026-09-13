from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel


class Source(BaseModel):
    """一个抓取数据源（站点栏目）。key 作为去重命名空间，name 用于卡片展示。"""

    key: str
    name: str
    enabled: bool = True


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


class VideoInfo(BaseModel):
    object_id: str = ""
    title: str = ""
    duration: float | None = None
    play_count: str = ""


class Post(BaseModel):
    """统一数据契约。字段名沿用微博项目以复用投递层，语义重映射到「文章」：

    - uid          -> 数据源 key（去重命名空间），如 "autohome-newbrand"
    - screen_name  -> 数据源展示名，如 "汽车之家·上市新车"
    - mid          -> 文章在本数据源内的稳定唯一 ID（去重键）
    - url          -> 文章原文链接
    - created_at   -> 文章发布/上市日期（tz-aware，统一 UTC；仅有日期时取当日 00:00）
    - title        -> 文章标题
    - text_plain   -> 供分类与兼容旧投递逻辑的文本（标题 + 正文）
    - full_text    -> 详情页提取的完整正文（不含标题）
    - image_urls   -> 封面图（0 或多张，取第一张上传飞书）

    转发/视频等微博特有字段保留默认值，投递层按缺省逻辑处理。
    """

    uid: str
    screen_name: str = ""
    mid: str
    bid: str = ""
    url: str
    created_at: dt.datetime
    created_at_is_precise: bool = False
    title: str = ""
    is_pinned: bool = False
    is_repost: bool = False
    text_html: str = ""
    text_plain: str = ""
    full_text: str = ""
    source: str = ""
    region_name: str = ""
    reposts_count: int = 0
    comments_count: int = 0
    attitudes_count: int = 0
    image_urls: list[str] = []
    video: VideoInfo | None = None
    retweeted_screen_name: str = ""
    retweeted_text_plain: str = ""
    text_truncated: bool = False
