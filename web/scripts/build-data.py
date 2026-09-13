"""把 auto-news-monitor 的 digest jsonl 转成前端用的 JSON 快照。

用法: python3 -u scripts/build-data.py [--source DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = os.path.expanduser(
    "~/work/auto-news-monitor/code/auto-news-monitor/state/digest"
)
DEFAULT_OUT = "src/data/news.json"
DEFAULT_TRANSLATIONS = "src/data/translations.en.json"
DEFAULT_BRANDS = "src/data/brands.json"

# 汇率：构建时拉一次并固化进快照，前端不发网络请求。
RATE_API = "https://open.er-api.com/v6/latest/CNY"
# 拉取失败时的兜底值，会在快照里标记 stale=true，前端据此提示汇率可能过期。
FALLBACK_CNY_USD = 0.1482

CST = timezone(timedelta(hours=8))

# 分类 -> 展示用的短标签与配色 key（对应 CSS 里的 --accent-*）
LABEL_META = {
    "产品发布": {"slug": "launch", "accent": "cyan", "en": "Product Launch"},
    "市场数据": {"slug": "market", "accent": "emerald", "en": "Market Data"},
    "车圈热点": {"slug": "hot", "accent": "rose", "en": "Industry Buzz"},
}
FALLBACK_LABEL = {"slug": "other", "accent": "amber", "en": "Other"}

# 来源站点与栏目的英文名
SITE_EN = {
    "易车": "Yiche",
    "新浪汽车": "Sina Auto",
    "汽车之家": "Autohome",
}
SOURCE_EN = {
    "易车·易车原创": "Yiche · Originals",
    "新浪汽车·新车资讯": "Sina Auto · New Cars",
    "汽车之家·上市新车": "Autohome · Launches",
}

# 关注度占位算法的权重。真实互动数据（阅读/转发/评论）接入后应整体替换 compute_heat。
LABEL_WEIGHT = {"车圈热点": 34, "市场数据": 26, "产品发布": 18}
SOURCE_WEIGHT = {
    "易车·易车原创": 22,
    "汽车之家·上市新车": 18,
    "新浪汽车·新车资讯": 16,
}
HOT_WORDS = ("上市", "交付", "预售", "降价", "价格战", "销量", "发布", "首发", "官宣")


def compute_heat(row: dict) -> int:
    """派生一个 0-100 的关注度分。

    占位实现：只用条目自身的元信息，保证同一条数据每次构建结果一致。
    等抓取端拿到真实互动指标后，这个函数应当被整体替换，而不是继续加权重。
    """
    score = LABEL_WEIGHT.get(row["label"], 16)
    score += SOURCE_WEIGHT.get(row["source"], 14)
    title = row["title"]
    score += min(sum(4 for w in HOT_WORDS if w in title), 16)
    score += min(len(re.findall(r"\d+", title)) * 3, 12)
    # 用 mid 做一点确定性抖动，避免同类条目热度完全并列
    jitter = int(hashlib.sha1(row["mid"].encode()).hexdigest()[:4], 16) % 11
    return max(1, min(100, score + jitter))


def fetch_rate() -> dict:
    """拉 CNY->USD 汇率。失败不中断构建，退回兜底值并标记 stale。"""
    try:
        with urllib.request.urlopen(RATE_API, timeout=15) as resp:
            payload = json.load(resp)
        rate = payload["rates"]["USD"]
        logger.info("汇率 CNY->USD = %s (%s)", rate, payload.get("time_last_update_utc"))
        return {
            "cnyToUsd": rate,
            "fetchedAt": payload.get("time_last_update_utc", ""),
            "source": "open.er-api.com",
            "stale": False,
        }
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as exc:
        logger.warning("汇率拉取失败，改用兜底值 %s：%s", FALLBACK_CNY_USD, exc)
        return {
            "cnyToUsd": FALLBACK_CNY_USD,
            "fetchedAt": "",
            "source": "fallback",
            "stale": True,
        }


def load_brands(path: str) -> list[dict]:
    """品牌配置。别名按长度倒序，保证 "Dongfeng Nissan" 先于 "Nissan" 匹配。"""
    with open(path, encoding="utf-8") as fh:
        brands = json.load(fh)["brands"]
    for b in brands:
        b["_patterns"] = [
            re.compile(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", re.IGNORECASE)
            for a in sorted(b["aliases"], key=len, reverse=True)
        ]
    logger.info("读取 %d 个品牌", len(brands))
    return brands


def match_brands(item: dict, brands: list[dict]) -> list[str]:
    """从标题和要点里匹配品牌 slug，按 exportRank 排序。一条可命中多个品牌。"""
    blob = item["title"] + " " + " ".join(item["points"])
    hits = [b for b in brands if any(p.search(blob) for p in b["_patterns"])]
    hits.sort(key=lambda b: b["exportRank"])
    return [b["slug"] for b in hits]


def load_translations(path: str) -> dict:
    """按条目 id 索引的英文翻译。缺文件不报错，只是全部条目回退中文。"""
    if not os.path.exists(path):
        logger.warning("没有翻译文件 %s，条目将保持中文", path)
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    logger.info("读取 %d 条翻译", len(data))
    return data


def load_rows(source_dir: str) -> list[dict]:
    files = sorted(glob.glob(os.path.join(source_dir, "*.jsonl")))
    if not files:
        raise FileNotFoundError(f"没有找到 digest 文件: {source_dir}")
    rows: list[dict] = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno} 不是合法 JSON") from exc
    logger.info("读取 %d 个文件，共 %d 条", len(files), len(rows))
    return rows


def transform(row: dict, translations: dict) -> dict:
    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    local = created.astimezone(CST)
    meta = LABEL_META.get(row["label"], FALLBACK_LABEL)
    points = [
        p.lstrip("- ").strip()
        for p in row["summary"].splitlines()
        if p.strip().startswith("-")
    ]

    # 有英文翻译就用，没有就回退中文并标记，供前端提示
    tr = translations.get(row["mid"])
    translated = bool(tr)
    title = tr["title"] if translated else row["title"]
    if translated:
        points = tr["points"]

    site = row["source"].split("·")[0]
    return {
        "id": row["mid"],
        "title": title,
        "points": points,
        "translated": translated,
        "label": meta["en"],
        "labelSlug": meta["slug"],
        "accent": meta["accent"],
        "source": SOURCE_EN.get(row["source"], row["source"]),
        "sourceSite": SITE_EN.get(site, site),
        "url": row["url"],
        "publishedAt": created.isoformat().replace("+00:00", "Z"),
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H:%M"),
        "heat": compute_heat(row),
        "featured": False,
    }


def mark_featured(items: list[dict]) -> None:
    """每天关注度最高的一条标为精选。

    不用固定阈值：heat 是占位值，阈值会让某些天全中、某些天一条不中。
    按天取头名能保证每天恰好有一个视觉落点。
    """
    best: dict[str, dict] = {}
    for it in items:
        cur = best.get(it["date"])
        if cur is None or it["heat"] > cur["heat"]:
            best[it["date"]] = it
    for it in best.values():
        it["featured"] = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--translations", default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--brands", default=DEFAULT_BRANDS)
    args = parser.parse_args()

    translations = load_translations(args.translations)
    brands = load_brands(args.brands)
    items = [transform(r, translations) for r in load_rows(args.source)]
    items.sort(key=lambda it: it["publishedAt"], reverse=True)
    mark_featured(items)

    for it in items:
        it["brands"] = match_brands(it, brands)

    # 只输出实际命中过的品牌，避免前端渲染一排空品牌
    counts: dict[str, int] = {}
    for it in items:
        for slug in it["brands"]:
            counts[slug] = counts.get(slug, 0) + 1
    brand_out = [
        {
            "slug": b["slug"],
            "name": b["name"],
            "origin": b["origin"],
            "exportRank": b["exportRank"],
            "count": counts[b["slug"]],
        }
        for b in sorted(brands, key=lambda b: b["exportRank"])
        if b["slug"] in counts
    ]
    logger.info(
        "品牌命中 %d/%d，未命中: %s",
        len(brand_out),
        len(brands),
        ", ".join(b["slug"] for b in brands if b["slug"] not in counts) or "无",
    )
    no_brand = sum(1 for it in items if not it["brands"])
    if no_brand:
        logger.warning("%d 条没有匹配到任何品牌", no_brand)

    labels = []
    seen = set()
    for it in items:
        if it["label"] not in seen:
            seen.add(it["label"])
            labels.append(
                {"name": it["label"], "slug": it["labelSlug"], "accent": it["accent"]}
            )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "rate": fetch_rate(),
        "labels": labels,
        "brands": brand_out,
        "sources": sorted({it["source"] for it in items}),
        "items": items,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    untranslated = sum(1 for it in items if not it["translated"])
    if untranslated:
        logger.warning("%d 条没有英文翻译，已回退中文", untranslated)
    logger.info("写出 %d 条到 %s", len(items), args.out)


if __name__ == "__main__":
    main()
