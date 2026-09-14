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
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger(__name__)

# 单仓库内的相对路径，不再依赖某台机器上的绝对路径。
# 容器里用 --source 指到挂载的 state volume。
DEFAULT_SOURCE = str(Path(__file__).resolve().parents[2] / "scraper" / "state" / "digest")
DEFAULT_OUT = "src/data/news.json"
DEFAULT_TRANSLATIONS = "src/data/translations.en.json"
DEFAULT_BRANDS = "src/data/brands.json"
DEFAULT_PUBLIC_DIR = "public"
# RSS 自引用链接需要站点公开地址；没配时用占位值并告警，不让构建失败。
PLACEHOLDER_BASE_URL = "https://autohot.example"

# 汇率：构建时拉一次并固化进快照，前端不发网络请求。
RATE_API = "https://open.er-api.com/v6/latest/CNY"
# 拉取失败时的兜底值，会在快照里标记 stale=true，前端据此提示汇率可能过期。
FALLBACK_CNY_USD = 0.1482

CST = timezone(timedelta(hours=8))

# 分类 -> 展示用的短标签与配色 key（对应 CSS 里的 --accent-*）
#
# 抓取端产出 10 个分类（「广告」「汽车无关」在推送前就被丢弃，到不了这里），
# 这里必须覆盖剩下的 8 个，否则未映射的会全部塌成 "Other"。
#
# accent 只有 cyan/emerald/rose/amber 四个值（globals.css 的 token 与 lib/news.ts
# 的 Accent 联合类型都是这四个），所以按语义归组、两两共用一色，不要新增颜色。
LABEL_META = {
    # 产品线
    "产品发布": {"slug": "launch", "accent": "cyan", "en": "Product Launch"},
    "谍照申报": {"slug": "spy", "accent": "cyan", "en": "Spy Shots"},
    # 数字与资本
    "市场数据": {"slug": "market", "accent": "emerald", "en": "Market Data"},
    "资本市场": {"slug": "capital", "accent": "emerald", "en": "Capital Markets"},
    # 热点与出海
    "车圈热点": {"slug": "hot", "accent": "rose", "en": "Industry Buzz"},
    "出海信息": {"slug": "export", "accent": "rose", "en": "Going Global"},
    # 政策与分析
    "政策监管": {"slug": "policy", "accent": "amber", "en": "Policy"},
    "行业观察": {"slug": "analysis", "accent": "amber", "en": "Analysis"},
}
FALLBACK_LABEL = {"slug": "other", "accent": "amber", "en": "Other"}

# 来源站点与栏目的英文名
SITE_EN = {
    "易车": "Yiche",
    "新浪汽车": "Sina Auto",
    "汽车之家": "Autohome",
}
SOURCE_EN = {
    "易车·易车原创": "Yiche Originals",
    "新浪汽车·新车资讯": "Sina Auto New Cars",
    "汽车之家·上市新车": "Autohome Launches",
}
# 微博来源是「微博·<博主名>」，博主名逐条不同，没法穷举，
# 统一显示成 Weibo 加博主名（博主名保持原样，不硬译人名）。
WEIBO_SITE_EN = "Weibo"

# 关注度占位算法的权重。真实互动数据（阅读/转发/评论）接入后应整体替换 compute_heat。
LABEL_WEIGHT = {"车圈热点": 34, "市场数据": 26, "产品发布": 18}
SOURCE_WEIGHT = {
    "易车·易车原创": 22,
    "汽车之家·上市新车": 18,
    "新浪汽车·新车资讯": 16,
}
HOT_WORDS = ("上市", "交付", "预售", "降价", "价格战", "销量", "发布", "首发", "官宣")
# 微博是观点不是通稿，给一个中性权重；博主名无法穷举，走不到 SOURCE_WEIGHT
WEIBO_SOURCE_WEIGHT = 20


def compute_heat(row: dict) -> int:
    """派生一个 0-100 的关注度分。

    占位实现：只用条目自身的元信息，保证同一条数据每次构建结果一致。
    等抓取端拿到真实互动指标后，这个函数应当被整体替换，而不是继续加权重。
    """
    score = LABEL_WEIGHT.get(row["label"], 16)
    if row.get("kind") == "weibo":
        score += WEIBO_SOURCE_WEIGHT
    else:
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


CJK_RE = re.compile(r"[一-鿿]")

# 「¥12,000 units」这种把数量写成金额的错误。Money.tsx 只按 ¥ 认金额，会把它
# 换算成美元，页面上出现无意义的「$1,780 units」且没有任何报错。
#
# 这段与 scraper/src/translate.py 的同名函数重复，但那是必要的：build-data.py
# 是纯标准库脚本、跑在不含 scraper 源码的镜像里，import 不到。更重要的是人工维护的
# translations.en.json 根本不经过 scraper，这里才是人工与机翻两路唯一的汇合点。
_UNIT_WORDS = r"(?:units?|vehicles?|cars?|deliveries|orders?|sales)"
MONEY_UNIT_RE = re.compile(rf"¥([\d,]+(?:\.\d+)?)(\s*{_UNIT_WORDS}\b)", re.IGNORECASE)


def split_points(summary: str) -> list[str]:
    """digest 的 summary 是「- 要点」多行字符串，拆回列表。"""
    return [
        line.lstrip("- ").strip()
        for line in summary.splitlines()
        if line.strip().startswith("-")
    ]


def fix_money_units(text: str) -> str:
    """把「¥12,000 units」修成「12,000 units」。数量单位词前的 ¥ 一定是错的。"""
    fixed = MONEY_UNIT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}", text)
    if fixed != text:
        logger.warning("数量误带货币符号，已修正：%s", text[:80])
    return fixed


# 从英文里兜底提取一个关键数字。优先级与 translate.py 的 prompt 一致：
# 金额 > 带单位的数量 > 其他带单位的数字。
_FIGURE_PATTERNS = (
    r"¥[\d,]+(?:\.\d+)?",
    rf"[\d,]+(?:\.\d+)?\s*{_UNIT_WORDS}",
    r"[\d,]+(?:\.\d+)?\s*(?:km|kWh|hp|mm|%)",
)


def extract_figure(title: str, points: list[str]) -> str:
    """没有 figure_en 时的兜底。

    抓取端上线前的历史条目只有人工翻译，没有这个字段；正则提取让这些条目
    在版式上不至于整片空缺。新条目有 LLM 挑的字段，走不到这里。
    """
    for text in (title, *points):
        for pattern in _FIGURE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                # [\d,]+ 会把句中的尾逗号一起吞进来，形成「¥59,600,」
                return " ".join(m.group(0).split()).rstrip(",.;:")[:24]
    return ""


def _usable_english(title: str, points: list[str]) -> tuple[str, list[str]] | None:
    """英文必须成套且确实是英文，否则宁可整条回退中文。

    半中半英比纯中文更糟：页面上一半是英文一半是汉字，而且 translated 标记会
    骗过 /about 页的未翻译计数，让它失去质量指标的意义。

    通过校验后统一修一遍金额写法——人工翻译不经过 scraper 的任何检查，
    这里是它唯一的防线。
    """
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(points, list) or not points:
        return None
    if not all(isinstance(p, str) and p.strip() for p in points):
        return None
    if CJK_RE.search(title) or any(CJK_RE.search(p) for p in points):
        return None
    return fix_money_units(title), [fix_money_units(p) for p in points]


def _pick_english(row: dict, translations: dict) -> tuple[str, list[str]] | None:
    """人工覆盖 > 抓取端机翻 > 无。两者都要过 _usable_english。"""
    manual = translations.get(row["mid"])
    if isinstance(manual, dict):
        picked = _usable_english(manual.get("title", ""), manual.get("points", []))
        if picked is not None:
            return picked
        logger.warning("manual translation unusable, falling through: mid=%s", row["mid"])

    return _usable_english(
        row.get("title_en", ""), split_points(row.get("summary_en", ""))
    )


def transform(row: dict, translations: dict) -> dict:
    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    local = created.astimezone(CST)
    meta = LABEL_META.get(row["label"], FALLBACK_LABEL)

    # 英文三级优先：人工覆盖 > 抓取端机翻 > 回退中文并标记，供前端提示。
    # 人工排第一是为了能手工修正机翻而不必改抓取端；抓取端上线前的历史条目
    # 也只有人工翻译这一份。
    english = _pick_english(row, translations)
    translated = english is not None
    title, points = english if translated else (row["title"], split_points(row["summary"]))

    kind = row.get("kind", "web")
    site = row["source"].split("·")[0]
    if kind == "weibo":
        # 「微博·某博主」-> source 用博主名，sourceSite 统一为 Weibo
        handle = row["source"].split("·", 1)[-1]
        source_en, site_en = handle, WEIBO_SITE_EN
    else:
        source_en = SOURCE_EN.get(row["source"], row["source"])
        site_en = SITE_EN.get(site, site)
    # 扫读锚点。优先用翻译环节挑的（它看得懂上下文），没有就正则兜底。
    figure = row.get("figure_en", "").strip()
    if not figure and translated:
        figure = extract_figure(title, points)
    # 标题里已经有这个数字就不再单独显示：同一个数字隔几个词出现两次，
    # 看着像渲染错误。翻译 prompt 已要求标题不重复 figure，这里兜住
    # 历史条目（它们的 figure 本来就是从标题里正则提取的）和模型偶尔不听话。
    if figure and figure.lower() in title.lower():
        figure = ""
    return {
        "id": row["mid"],
        "kind": kind,
        "figure": figure,
        "title": title,
        "points": points,
        "translated": translated,
        "label": meta["en"],
        "labelSlug": meta["slug"],
        "accent": meta["accent"],
        "source": source_en,
        "sourceSite": site_en,
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


ATOM_NS = "http://www.w3.org/2005/Atom"
RSS_ITEM_LIMIT = 50

FEED_META = {
    "en": {
        "path": "rss.xml",
        "language": "en",
        "title": "AUTOHOT — China Auto Industry News",
        "description": (
            "Daily news from China's auto industry: new car launches, "
            "sales data and industry buzz."
        ),
    },
    "zh": {
        "path": "rss.zh.xml",
        "language": "zh-CN",
        "title": "AUTOHOT — 中国汽车行业资讯",
        "description": "每天的中国汽车行业新闻：新车上市、销量数据与车圈热点。",
    },
}


def _rss_description(points: list[str]) -> str:
    """要点渲染成 HTML 列表。

    这里先按 HTML 转义一次，ElementTree 写出时会再按 XML 转义一次；
    阅读器解一次 XML 转义后拿到的正是合法 HTML。
    """
    body = "".join(f"<li>{xml_escape(p)}</li>" for p in points if p)
    return f"<ul>{body}</ul>" if body else ""


def build_feed(
    entries: list[dict],
    *,
    meta: dict,
    base_url: str,
    generated_at: datetime,
) -> bytes:
    base = base_url.rstrip("/")

    rss = ET.Element("rss", {"version": "2.0", "xmlns:atom": ATOM_NS})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = meta["title"]
    ET.SubElement(channel, "link").text = base + "/"
    ET.SubElement(channel, "description").text = meta["description"]
    ET.SubElement(channel, "language").text = meta["language"]
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        generated_at, usegmt=True
    )
    ET.SubElement(
        channel,
        "atom:link",
        {"href": f"{base}/{meta['path']}", "rel": "self", "type": "application/rss+xml"},
    )

    for entry in entries[:RSS_ITEM_LIMIT]:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = entry["title"]
        ET.SubElement(node, "link").text = entry["url"]
        ET.SubElement(node, "description").text = _rss_description(entry["points"])
        ET.SubElement(node, "category").text = entry["label"]
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = entry["id"]
        ET.SubElement(node, "pubDate").text = format_datetime(
            datetime.fromisoformat(entry["publishedAt"].replace("Z", "+00:00")),
            usegmt=True,
        )

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def write_feeds(
    items: list[dict],
    rows_by_mid: dict[str, dict],
    *,
    public_dir: str,
    base_url: str,
    generated_at: datetime,
) -> None:
    """写出中英两个 feed。

    英文取 items（已应用翻译，未翻译的条目带中文回退，与网站显示一致，
    不静默丢条目）。中文直接取原始 digest 行，因此不依赖翻译链路——
    DeepSeek 挂了中文 feed 照常完整发布。
    """
    # 中文条目就是英文条目换掉三个语言相关字段。用 {**it} 而不是重列字段，
    # 这样以后 build_feed 多读一个字段时中文 feed 会自动跟上。
    # items 本就由 rows 逐条 transform 而来，rows_by_mid[it["id"]] 必定存在。
    zh_entries = [
        {
            **it,
            "title": rows_by_mid[it["id"]]["title"],
            "points": split_points(rows_by_mid[it["id"]].get("summary", "")),
            "label": rows_by_mid[it["id"]].get("label", ""),
        }
        for it in items[:RSS_ITEM_LIMIT]
    ]

    os.makedirs(public_dir, exist_ok=True)
    for lang, entries in (("en", items), ("zh", zh_entries)):
        meta = FEED_META[lang]
        path = os.path.join(public_dir, meta["path"])
        with open(path, "wb") as fh:
            fh.write(
                build_feed(
                    entries, meta=meta, base_url=base_url, generated_at=generated_at
                )
            )
        logger.info("写出 %d 条到 %s", min(len(entries), RSS_ITEM_LIMIT), path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--translations", default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--brands", default=DEFAULT_BRANDS)
    parser.add_argument("--public-dir", default=DEFAULT_PUBLIC_DIR)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SITE_BASE_URL", ""),
        help="站点公开地址，用于 RSS 的自引用链接。也可用 SITE_BASE_URL 环境变量。",
    )
    args = parser.parse_args()

    base_url = args.base_url or PLACEHOLDER_BASE_URL
    if base_url == PLACEHOLDER_BASE_URL:
        logger.warning(
            "未设置 --base-url / SITE_BASE_URL，RSS 自引用链接会是占位值 %s，"
            "上线前必须设成真实域名",
            PLACEHOLDER_BASE_URL,
        )

    translations = load_translations(args.translations)
    brands = load_brands(args.brands)
    rows = load_rows(args.source)
    # 中文 feed 要用原始中文字段，而 transform 只留英文，所以按 mid 留一份索引
    rows_by_mid = {r["mid"]: r for r in rows}
    items = [transform(r, translations) for r in rows]
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

    generated_at = datetime.now(timezone.utc)
    payload = {
        "generatedAt": generated_at.isoformat().replace("+00:00", "Z"),
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

    write_feeds(
        items,
        rows_by_mid,
        public_dir=args.public_dir,
        base_url=base_url,
        generated_at=generated_at,
    )


if __name__ == "__main__":
    main()
