"""把日报写成飞书文档。

卡片适合当天在群里扫一眼，文档是可回看、可检索的档案，容量也不受卡片
上限约束。详讯在文档里五要素齐全：标题与中文缩写默认展开，英文对照、
图片、原文各挂一个默认折叠的小节；短讯合并在末尾（散装观点全量收录）。

软依赖：创建失败只记日志，日报卡片照发。需要应用开通权限：
- docx:document（创建及编辑新版文档）
- drive:drive 或 drive:permission（设置链接分享，让群成员可读）
- drive 文件上传权限（文档配图；缺权限时降级为无图文档）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
import lark_oapi as lark
from lark_oapi.api.docx.v1 import (
    Block,
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
    CreateDocumentRequest,
    CreateDocumentRequestBody,
    Image,
    Link,
    Text,
    TextElement,
    TextElementStyle,
    TextRun,
    TextStyle,
)
from lark_oapi.api.drive.v2 import (
    PatchPermissionPublicRequest,
    PermissionPublic,
)

from ..card import CST
from ..config import Settings
from .card import _stray_worth_showing, event_display_title, event_points_zh, heat
from .doc_images import bind_image, download_images
from .events import Event
from .result import DigestResult

logger = logging.getLogger(__name__)

# docx 块类型码
_TYPE = {"text": 2, "h2": 4, "h3": 5, "h4": 6, "bullet": 12, "quote": 15, "image": 27}
_BATCH = 50
_GREY = 7  # docx 字色枚举：灰

_FULL_TEXT_MAX_CHARS = 5000
# 文档是档案定位，超长微博（长文可达数千字）不该在这里被截掉
_WEIBO_QUOTE_MAX_CHARS = 2000
_BLOCK_TEXT_MAX_CHARS = 2000  # 单个 docx 文本块的保守长度上限


@dataclass
class DocLine:
    """文档的一行：块类型 + 富文本片段（文本, 链接）。纯数据，便于单测。"""

    kind: str
    runs: list[tuple[str, str | None]] = field(default_factory=list)
    folded: bool = False  # 标题块默认折叠其后属内容
    grey: bool = False  # 整行灰字（时间、来源等元信息）
    image_url: str = ""  # kind == "image" 时的原始图片 URL


def _line(
    kind: str, *runs: tuple[str, str | None], folded: bool = False, grey: bool = False
) -> DocLine:
    return DocLine(kind=kind, runs=list(runs), folded=folded, grey=grey)


def event_image_urls(event: Event, limit: int) -> list[str]:
    """事件配图候选：网站封面图（主源优先）在前，其后按微博时间序，去重取前 N。"""
    if limit <= 0:
        return []
    urls: list[str] = []
    for record in [event.primary, *event.others]:
        urls.extend(record.image_urls)
    weibo = sorted(event.weibo_records, key=lambda r: r.created_at)
    for record in weibo:
        urls.extend(record.image_urls)
    return list(dict.fromkeys(urls))[:limit]


def collect_image_urls(result: DigestResult, max_per_event: int) -> list[str]:
    urls: list[str] = []
    for event in result.events:
        if event.is_feature:
            urls.extend(event_image_urls(event, max_per_event))
    return list(dict.fromkeys(urls))


def _source_lines(event: Event) -> DocLine:
    sources: list[tuple[str, str | None]] = []
    seen_sources: set[str] = set()
    for record in [event.primary, *event.others]:
        if record.source in seen_sources:
            continue
        seen_sources.add(record.source)
        if sources:
            sources.append((" / ", None))
        sources.append((record.source, record.url or None))
    moment = event.primary.created_at.astimezone(CST).strftime("%H:%M")
    return _line("text", (f"{moment} · ", None), *sources, grey=True)


def _feature_lines(
    event: Event,
    discussions: list,
    available_images: set[str],
    max_images_per_event: int,
) -> list[DocLine]:
    """一条详讯：标题+中文缩写默认展开；英文/图片/原文各一个折叠 h4 小节。

    h4 折叠域到下一个同级或更高级标题为止，下一条详讯的 h3 自然终结它。
    """
    primary = event.primary
    lines = [_line("h3", (event_display_title(event), primary.url or None))]
    for point in event_points_zh(event):
        lines.append(_line("bullet", (point, None)))
    lines.append(_source_lines(event))

    if event.brief:
        english = [en for _, en in event.brief.pairs if en]
        if english:
            lines.append(_line("h4", ("English", None), folded=True))
            lines.extend(_line("bullet", (en, None)) for en in english)

    images = [
        url
        for url in event_image_urls(event, max_images_per_event)
        if url in available_images
    ]
    if images:
        lines.append(_line("h4", ("图片", None), folded=True))
        lines.extend(DocLine(kind="image", image_url=url) for url in images)

    original = _original_lines(event, discussions)
    if original:
        lines.append(_line("h4", ("原文", None), folded=True))
        lines.extend(original)
    return lines


def _chunk(text: str, size: int = _BLOCK_TEXT_MAX_CHARS) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _original_lines(event: Event, discussions: list) -> list[DocLine]:
    """「原文」小节。精修跑过时逐行渲染 detail_material——保证与 LLM 输入一致；
    没跑过（精修关闭/短讯）退回结构化拼装。"""
    if event.detail_material:
        lines: list[DocLine] = []
        for raw_line in event.detail_material.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            for piece in _chunk(raw_line):
                lines.append(_line("text", (piece, None)))
        return lines

    primary = event.primary
    original: list[DocLine] = []
    body = (primary.full_text or primary.summary).strip()
    if body and primary.kind == "web":
        for paragraph in body[:_FULL_TEXT_MAX_CHARS].split("\n"):
            paragraph = paragraph.strip()
            if paragraph:
                original.append(_line("text", (paragraph, None)))
    for record in event.weibo_records if primary.kind == "web" else event.records:
        if not _stray_worth_showing(record):
            continue
        text = " ".join((record.full_text or record.title).split())[:_WEIBO_QUOTE_MAX_CHARS]
        original.append(
            _line("quote", (record.source, record.url), (f" {text} ", None), ("原微博", record.url))
        )
    for pick in discussions:
        text = " ".join(pick.hit.text.split())[:_WEIBO_QUOTE_MAX_CHARS]
        angle = f"（{pick.angle}）" if pick.angle else ""
        original.append(
            _line(
                "quote",
                (f"@{pick.hit.screen_name}", pick.hit.url),
                (f"{angle} {text} ", None),
                ("原微博", pick.hit.url),
            )
        )
    return original


def doc_outline(
    result: DigestResult,
    *,
    available_images: set[str] | None = None,
    max_images_per_event: int = 0,
) -> list[DocLine]:
    """日报 → 文档行序列。详讯按热度排序，短讯（单微博事件+散装观点）殿后。"""
    available = available_images or set()
    lines: list[DocLine] = []
    events = sorted(
        result.events,
        key=lambda e: heat(e, result.discussions.get(e.title, [])),
        reverse=True,
    )
    for event in events:
        if not event.is_feature:
            continue
        lines.extend(
            _feature_lines(
                event,
                result.discussions.get(event.title, []),
                available,
                max_images_per_event,
            )
        )

    shorts = [e.primary for e in events if not e.is_feature]
    shorts += [r for r in result.strays if _stray_worth_showing(r)]
    shorts.sort(key=lambda r: r.created_at)
    if shorts:
        lines.append(_line("h3", ("短讯", None), folded=True))
        for record in shorts:
            lines.append(
                _line(
                    "bullet",
                    (record.source, record.url),
                    (f" {record.title} ", None),
                    ("原文", record.url),
                )
            )
    return lines


def _to_block(line: DocLine) -> Block:
    if line.kind == "image":
        # 空占位块，发布后由 doc_images.bind_image 上传素材并绑定 token
        return Block.builder().block_type(_TYPE["image"]).image(Image.builder().build()).build()
    elements = []
    for text, url in line.runs:
        style = TextElementStyle.builder()
        if url:
            style = style.link(Link.builder().url(url).build())
        if line.grey:
            style = style.text_color(_GREY)
        run = TextRun.builder().content(text).text_element_style(style.build()).build()
        elements.append(TextElement.builder().text_run(run).build())
    body = Text.builder().elements(elements)
    if line.folded:
        body = body.style(TextStyle.builder().folded(True).build())
    builder = Block.builder().block_type(_TYPE[line.kind])
    # SDK 对不同块类型用不同字段名，内容结构相同
    setter = {
        "text": builder.text,
        "h2": builder.heading2,
        "h3": builder.heading3,
        "h4": builder.heading4,
        "bullet": builder.bullet,
        "quote": builder.quote,
    }[line.kind]
    return setter(body.build()).build()


class DigestDocError(Exception):
    pass


def _create_document(client: lark.Client, title: str, folder_token: str) -> str:
    body = CreateDocumentRequestBody.builder().title(title)
    if folder_token:
        body = body.folder_token(folder_token)
    request = CreateDocumentRequest.builder().request_body(body.build()).build()
    response = client.docx.v1.document.create(request)
    if not response.success():
        raise DigestDocError(f"document create failed: code={response.code} msg={response.msg}")
    document_id = response.data.document.document_id if response.data else ""
    if not document_id:
        raise DigestDocError("document create succeeded without document_id")
    return document_id


def _append_blocks(client: lark.Client, document_id: str, blocks: list[Block]) -> list[Block]:
    """分批追加块，返回服务端创建的块（含 block_id，与请求顺序一一对应）。"""
    created: list[Block] = []
    index = 0
    for start in range(0, len(blocks), _BATCH):
        chunk = blocks[start : start + _BATCH]
        request = (
            CreateDocumentBlockChildrenRequest.builder()
            .document_id(document_id)
            .block_id(document_id)
            .request_body(
                CreateDocumentBlockChildrenRequestBody.builder()
                .children(chunk)
                .index(index)
                .build()
            )
            .build()
        )
        response = client.docx.v1.document_block_children.create(request)
        if not response.success():
            raise DigestDocError(
                f"block append failed at {index}: code={response.code} msg={response.msg}"
            )
        created.extend(response.data.children or [] if response.data else [])
        index += len(chunk)
    return created


def _enable_link_share(client: lark.Client, document_id: str) -> None:
    """允许租户内成员通过链接阅读。失败不致命：文档仍在，只是要手动开分享。"""
    request = (
        PatchPermissionPublicRequest.builder()
        .token(document_id)
        .type("docx")
        .request_body(PermissionPublic.builder().link_share_entity("tenant_readable").build())
        .build()
    )
    response = client.drive.v2.permission_public.patch(request)
    if not response.success():
        logger.warning(
            "doc link share failed (需要 drive 权限，文档需手动开分享): code=%s msg=%s",
            response.code,
            response.msg,
        )


def _bind_images(
    client: lark.Client,
    document_id: str,
    outline: list[DocLine],
    created: list[Block],
    images: dict[str, bytes],
) -> None:
    """把下载好的图片绑定到对应占位块。任何失败只告警，不影响正文。"""
    if len(created) != len(outline):
        logger.warning(
            "created block count mismatch (%d != %d), skipping image binding",
            len(created),
            len(outline),
        )
        return
    bound = total = 0
    for line, block in zip(outline, created, strict=True):
        if line.kind != "image":
            continue
        total += 1
        data = images.get(line.image_url)
        if not data or not block.block_id:
            continue
        if bind_image(client, document_id, block.block_id, data):
            bound += 1
    if total:
        logger.info("doc images bound: %d/%d", bound, total)


def _publish(
    client: lark.Client,
    result: DigestResult,
    settings: Settings,
    outline: list[DocLine],
    images: dict[str, bytes],
) -> str:
    title = f"{result.day.strftime('%Y-%m-%d')} 汽车资讯日报"
    document_id = _create_document(client, title, settings.digest_doc_folder_token)
    blocks = [_to_block(line) for line in outline]
    created = _append_blocks(client, document_id, blocks)
    _bind_images(client, document_id, outline, created, images)
    _enable_link_share(client, document_id)
    url = f"{settings.digest_doc_domain.rstrip('/')}/docx/{document_id}"
    logger.info("digest doc published: %s", url)
    return url


async def publish_digest_doc(
    result: DigestResult,
    settings: Settings,
    lark_client: lark.Client | None,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """建文档并返回链接。任何失败返回 None，日报卡片照发。"""
    if not settings.digest_doc_enabled or lark_client is None or not result.events:
        return None
    images: dict[str, bytes] = {}
    if settings.digest_doc_images_enabled and http_client is not None:
        try:
            urls = collect_image_urls(result, settings.digest_max_images_per_event)
            images = await download_images(urls, settings, http_client)
        except Exception:
            logger.exception("doc image download crashed, publishing without images")
            images = {}
    outline = doc_outline(
        result,
        available_images=set(images),
        max_images_per_event=settings.digest_max_images_per_event,
    )
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, _publish, lark_client, result, settings, outline, images
        )
    except Exception:
        logger.exception("digest doc publish failed, cards will go out without it")
        return None
