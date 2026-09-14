from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys
from pathlib import Path

import httpx
import yaml

from src import log
from src.archive import DigestStore
from src.atomic_json import load_json_object
from src.config import Settings
from src.health import HealthStore
from src.models import Source
from src.monitor import Monitor
from src.pipeline import ArticlePipeline
from src.sources import build_fetchers
from src.state import StateStore

CST = dt.timezone(dt.timedelta(hours=8))


def load_sources(path: str | Path) -> list[Source]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = [Source(**item) for item in data.get("sources", [])]
    if not sources:
        raise RuntimeError(f"no sources configured in {path}")
    return sources


async def _run(settings: Settings, *, once: bool, dry_run: bool) -> None:
    logger = logging.getLogger(__name__)
    sources = load_sources(settings.sources_file)
    fetchers = build_fetchers(sources)
    if not fetchers:
        raise RuntimeError("no enabled sources")

    async with httpx.AsyncClient() as http_client:
        archive = None if dry_run else DigestStore(settings.digest_dir)
        state = StateStore(
            settings.state_file, settings.seen_mids_per_account, read_only=dry_run
        )
        health = HealthStore(settings.health_file, read_only=dry_run)
        pipeline = ArticlePipeline(settings, http_client, archive=archive, dry_run=dry_run)
        monitor = Monitor(settings, fetchers, state, pipeline, health, http_client)

        logger.info(
            "auto-news started: sources=%d interval=%ds dry_run=%s translate=%s",
            len(fetchers),
            settings.poll_interval_seconds,
            dry_run,
            settings.translate_enabled and bool(settings.deepseek_api_key),
        )
        if once:
            summary = await monitor.run_cycle()
            monitor.finish_once(summary)
            return

        # 只有轮询这一个常驻任务，不再需要多任务监管
        await monitor.run_forever()


def _self_check(settings: Settings, config_path: str | Path | None = None) -> None:
    sources = load_sources(settings.sources_file)
    build_fetchers(sources)
    if settings.classification_enabled and not settings.deepseek_api_key:
        # 分类是归档内容的唯一来源，没跑等于产出空标签，必须挡住
        raise RuntimeError("deepseek_api_key must be configured")

    for raw_path in (settings.state_file, settings.health_file):
        path = Path(raw_path)
        if path.exists():
            load_json_object(path)
        parent = path.parent
        if not parent.exists() or not os.access(parent, os.W_OK):
            raise RuntimeError(f"state directory is not writable: {parent}")

    # 网站的全部输入落在 digest_dir，而 DigestStore.append 把 OSError 吞掉只记日志，
    # 权限不对是完全静默的，所以这里必须挡住。目录可能还没建（append 会自己建），
    # 那就检查最近的已存在祖先。
    probe = Path(settings.digest_dir).resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        raise RuntimeError(f"digest directory is not writable: {settings.digest_dir}")

    if config_path is not None:
        path = Path(config_path)
        if path.exists() and path.stat().st_mode & 0o077:
            raise RuntimeError(f"sensitive file permissions must be 0600: {path}")

    print(f"self-check ok: sources={len(sources)}")


async def _probe(settings: Settings, key: str | None) -> int:
    logger = logging.getLogger(__name__)
    sources = load_sources(settings.sources_file)
    fetchers = build_fetchers(sources)
    target = fetchers[0]
    if key:
        target = next((f for f in fetchers if f.key == key), None)
        if target is None:
            logger.error("probe key is not configured/enabled: %s", key)
            return 1
    try:
        async with httpx.AsyncClient() as http_client:
            posts = await target.fetch(http_client)
    except Exception as exc:
        logger.error("probe failed: %s", exc)
        return 1
    print(f"probe ok: key={target.key} items={len(posts)}")
    for post in posts[:5]:
        created = post.created_at.astimezone(CST).strftime("%Y-%m-%d %H:%M")
        print(f"  [{created}] {post.title or post.text_plain[:40]} -> {post.url}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="抓取中国汽车资讯，分类翻译后归档成网站与 RSS 的数据源"
    )
    parser.add_argument("--config", metavar="PATH", help="YAML config file path")
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and classify but log instead of archiving"
    )
    parser.add_argument(
        "--self-check", action="store_true", help="validate local runtime without network access"
    )
    parser.add_argument(
        "--probe",
        nargs="?",
        const="",
        metavar="KEY",
        help="fetch one configured source without sending or writing state",
    )
    args = parser.parse_args()

    config_path = args.config or ("config.yaml" if Path("config.yaml").exists() else None)
    settings = Settings.from_yaml(config_path) if config_path else Settings()

    log.setup(level=settings.log_level, log_dir=settings.log_dir, console_log=settings.console_log)

    if args.self_check:
        try:
            _self_check(settings, config_path)
        except Exception as exc:
            logging.getLogger(__name__).critical("self-check failed: %s", exc)
            sys.exit(1)
        return

    if args.probe is not None:
        sys.exit(asyncio.run(_probe(settings, args.probe or None)))

    try:
        asyncio.run(_run(settings, once=args.once, dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.getLogger(__name__).critical("fatal: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
