from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys
from pathlib import Path

import httpx
import lark_oapi as lark
import yaml

from src import log
from src.atomic_json import load_json_object
from src.bitable import BitableSyncer
from src.card_store import CardStore
from src.config import Settings
from src.digest.runner import DigestScheduler
from src.digest.store import DigestStore
from src.forward import ForwardService, ForwardStore
from src.health import HealthStore
from src.listener import CardActionListener
from src.models import Source
from src.monitor import Monitor
from src.sender import PostPusher
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


def build_lark_client(settings: Settings) -> lark.Client:
    if not settings.app_id or not settings.app_secret:
        raise RuntimeError("app_id and app_secret must be configured")
    return lark.Client.builder().app_id(settings.app_id).app_secret(settings.app_secret).build()


async def _consume_forwards(listener: CardActionListener, service: ForwardService) -> None:
    logger = logging.getLogger(__name__)
    async for event in listener.listen():
        try:
            await service.process(event)
        except Exception:
            logger.exception("forward process failed: mid=%s", event.mid)


async def _run_supervised(monitor: Monitor, side_tasks: list[asyncio.Task]) -> None:
    monitor_task = asyncio.create_task(monitor.run_forever(), name="monitor")
    tasks = [monitor_task, *side_tasks]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                raise RuntimeError(f"essential task cancelled: {task.get_name()}")
            exc = task.exception()
            if exc is not None:
                raise RuntimeError(f"essential task died: {task.get_name()}: {exc}") from exc
            raise RuntimeError(f"essential task exited unexpectedly: {task.get_name()}")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run(settings: Settings, *, once: bool, dry_run: bool) -> None:
    logger = logging.getLogger(__name__)
    sources = load_sources(settings.sources_file)
    fetchers = build_fetchers(sources)
    if not fetchers:
        raise RuntimeError("no enabled sources")

    lark_client = None
    if settings.feishu_enabled and not dry_run:
        if not settings.chat_id:
            raise RuntimeError("chat_id must be configured (use --list-chats to pick one)")
        lark_client = build_lark_client(settings)

    async with httpx.AsyncClient() as http_client:
        # 「配置里开了飞书」和「本次运行真的能发」是两回事，各命名一次，全文只用这两个：
        # dry-run 时 settings.feishu_enabled 为真而 live_feishu 为假——卡片照常组装
        # 以便验证，只是不发出去。
        live_feishu = lark_client is not None
        forward_on = settings.forward_enabled and live_feishu
        card_store = CardStore(settings.card_store_file) if forward_on else None
        digest_store = (
            DigestStore(settings.digest_dir)
            if settings.digest_archive_enabled and not dry_run
            else None
        )

        state = StateStore(
            settings.state_file, settings.seen_mids_per_account, read_only=dry_run
        )
        health = HealthStore(settings.health_file, read_only=dry_run)
        pusher = PostPusher(
            settings,
            lark_client,
            http_client,
            card_store=card_store,
            digest_store=digest_store,
            dry_run=dry_run,
        )
        monitor = Monitor(settings, fetchers, state, pusher, health, http_client)

        logger.info(
            "auto-news-monitor started: sources=%d interval=%ds dry_run=%s "
            "feishu=%s forward=%s archive=%s report=%s translate=%s",
            len(fetchers),
            settings.poll_interval_seconds,
            dry_run,
            settings.feishu_enabled,
            forward_on,
            digest_store is not None,
            settings.digest_report_enabled and live_feishu,
            settings.translate_enabled and bool(settings.deepseek_api_key),
        )
        if once:
            summary = await monitor.run_cycle()
            monitor.finish_once(summary)
            return

        side_tasks: list[asyncio.Task] = []
        if forward_on:
            forward_store = ForwardStore(settings.forwarded_file)
            service = ForwardService(settings, lark_client, card_store, forward_store)
            listener = CardActionListener(settings, service.accept)
            side_tasks.append(
                asyncio.create_task(_consume_forwards(listener, service), name="forward-listener")
            )
            side_tasks.append(asyncio.create_task(service.run_forever(), name="forward-retry"))
            if settings.bitable_url:
                syncer = BitableSyncer(settings, lark_client, forward_store)
                side_tasks.append(
                    asyncio.create_task(syncer.run_forever(), name="bitable-sync")
                )
            else:
                logger.warning("forward enabled but bitable_url not set, archive stays local")
        if settings.digest_report_enabled and live_feishu:
            scheduler = DigestScheduler(settings, lark_client, http_client)
            side_tasks.append(
                asyncio.create_task(scheduler.run_forever(), name="digest-scheduler")
            )
        await _run_supervised(monitor, side_tasks)


async def _digest_once(settings: Settings, day_text: str, *, dry_run: bool) -> int:
    """手动出一份日报（默认前一自然日），发送后退出。已发过的日期会跳过。"""
    logger = logging.getLogger(__name__)
    day: dt.date | None = None
    if day_text:
        try:
            day = dt.date.fromisoformat(day_text)
        except ValueError:
            logger.error("invalid digest date (want YYYY-MM-DD): %s", day_text)
            return 1
    lark_client = None if dry_run else build_lark_client(settings)
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        scheduler = DigestScheduler(settings, lark_client, http_client, dry_run=dry_run)
        result = await scheduler.run_once(day)
    print(
        f"digest done: day={result.day} events={len(result.events)} "
        f"cards={len(result.cards)} dry_run={dry_run}"
    )
    return 0


def _self_check(settings: Settings, config_path: str | Path | None = None) -> None:
    sources = load_sources(settings.sources_file)
    build_fetchers(sources)
    if settings.feishu_enabled:
        if not settings.app_id or not settings.app_secret or not settings.chat_id:
            raise RuntimeError("app_id, app_secret and chat_id must be configured")
    elif settings.classification_enabled and not settings.deepseek_api_key:
        # 关掉飞书后网站是唯一出口，分类没跑等于产出空标签，必须挡住
        raise RuntimeError("deepseek_api_key must be configured when feishu is disabled")

    checked = [settings.state_file, settings.health_file]
    if settings.feishu_enabled:
        checked += [settings.card_store_file, settings.forwarded_file]
    if settings.digest_report_enabled:
        checked.append(settings.digest_state_file)
    for raw_path in checked:
        path = Path(raw_path)
        if path.exists():
            load_json_object(path)
        parent = path.parent
        if not parent.exists() or not os.access(parent, os.W_OK):
            raise RuntimeError(f"state directory is not writable: {parent}")

    if settings.digest_archive_enabled:
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
    parser = argparse.ArgumentParser(description="Auto news monitor: push new car news to Feishu")
    parser.add_argument("--config", metavar="PATH", help="YAML config file path")
    parser.add_argument("--list-chats", action="store_true", help="list chats the bot is in")
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and diff but log instead of sending"
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
    parser.add_argument(
        "--digest-once",
        nargs="?",
        const="",
        metavar="DATE",
        help="build and send the daily digest (default: yesterday, or YYYY-MM-DD)",
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

    if args.digest_once is not None:
        sys.exit(asyncio.run(_digest_once(settings, args.digest_once, dry_run=args.dry_run)))

    if args.list_chats:
        from src.chats import list_chats

        for chat_id, name in list_chats(build_lark_client(settings)):
            print(f"{chat_id}\t{name}")
        return

    try:
        asyncio.run(_run(settings, once=args.once, dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.getLogger(__name__).critical("fatal: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
