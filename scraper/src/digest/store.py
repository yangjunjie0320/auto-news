"""日报素材落盘。

推送成功时追加一行，按北京时间分文件。JSONL 追加写天然幂等、崩溃安全，
按天分文件便于清理。不复用 CardStore：那是上限 500 条的 FIFO 打分缓存，
会把当天素材冲掉。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

CN_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


class DigestRecord(BaseModel):
    """一条日报素材。字段是投递链路上已经算好的结果，日报不重新抓取、不重新分类。"""

    kind: str = "web"
    mid: str
    source: str
    title: str
    summary: str = ""
    label: str = ""
    url: str = ""
    created_at: dt.datetime
    # 详讯需要原文与配图；默认值保证旧格式 JSONL 行可正常反序列化
    full_text: str = ""
    image_urls: list[str] = Field(default_factory=list)
    # 英文对照，供英文站与英文 RSS 使用。是并列字段不是替换：中文字段还要供
    # 中文 RSS 和网站的热度打分（compute_heat 匹配中文关键词）使用。
    # 翻译失败时留空，网站端会回退中文并标记 translated=false。
    title_en: str = ""
    summary_en: str = ""


def cn_date(moment: dt.datetime) -> dt.date:
    return moment.astimezone(CN_TZ).date()


class DigestStore:
    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    def _path(self, day: dt.date) -> Path:
        return self._dir / f"{day.isoformat()}.jsonl"

    def append(self, record: DigestRecord) -> None:
        """按记录自身的北京时间日期落盘。写失败只记日志，绝不影响推送。"""
        path = self._path(cn_date(record.created_at))
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            line = record.model_dump_json() + "\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            logger.exception("digest record append failed: mid=%s", record.mid)

    def load_day(self, day: dt.date) -> list[DigestRecord]:
        path = self._path(day)
        if not path.exists():
            return []
        records: list[DigestRecord] = []
        seen: set[str] = set()
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = DigestRecord.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError):
                # 单行损坏（写入中途崩溃）不该让整份日报发不出去
                logger.warning("skipping malformed digest line: %s:%d", path, index)
                continue
            if record.mid in seen:
                continue
            seen.add(record.mid)
            records.append(record)
        records.sort(key=lambda r: r.created_at)
        return records
