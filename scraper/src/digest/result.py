"""日报产出的数据载体。独立成模块：runner 和 doc 都要用，避免互相 import。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .events import Event
from .store import DigestRecord
from .weibo import Discussion


@dataclass
class DigestResult:
    day: dt.date
    events: list[Event] = field(default_factory=list)
    discussions: dict[str, list[Discussion]] = field(default_factory=dict)
    strays: list[DigestRecord] = field(default_factory=list)
    intro: str = ""
    cards: list[dict] = field(default_factory=list)
