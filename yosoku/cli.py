"""コマンドラインインターフェース.

  python -m yosoku run                       # 1回 収集→分析→通知
  python -m yosoku run --dry-run             # 通知せずシグナル表示
  python -m yosoku watch --interval 300      # 常駐ポーリング
  python -m yosoku sources                   # 収集のみ(疎通確認)
  python -m yosoku history --limit 20        # 過去シグナルの一覧
  python -m yosoku backtest 20260601 20260607 --horizon 5   # バックテスト
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from yosoku.config import Config, load_config
from yosoku.pipeline import build_pipeline, build_sources
from yosoku.store import open_store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _missing_secrets(cfg: Config, require_notify: bool) -> list[str]:
    missing = []
    if not cfg.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY(分析に必須)")
    if require_notify and not cfg.discord_webhook_url:
        missing.append("DISCORD_WEBHOOK_URL(通知に必須。--dry-run なら不要)")
    return missing


def _print_missing(missing: list[str]) -> int:
    print("環境変数が不足しています:", file=sys.stderr)
    for m in missing:
        print(f"  - {m}", file=sys.stderr)
    return 2


# ---- run / watch ----------------------------------------------------------


async def _run_once(cfg: Config, dry_run: bool) -> None:
    with open_store(cfg.store_path) as store:
        pipeline = build_pipeline(cfg, store, dry_run=dry_run)
        try:
            result = await pipeline.run_once()
        finally:
            await pipeline.aclose()
    print(
        f"collected={result.collected} new={result.new} "
        f"analyzed={result.analyzed} notified={result.notified} "
        f"cost=${result.cost:.4f}"
    )
    if result.usage_summary:
        print(f"usage: {result.usage_summary}")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _missing_secrets(cfg, require_notify=not args.dry_run)
    if missing:
        return _print_missing(missing)
    asyncio.run(_run_once(cfg, args.dry_run))
    return 0


async def _watch(cfg: Config, dry_run: bool, interval, max_runtime) -> None:
    with open_store(cfg.store_path) as store:
        pipeline = build_pipeline(cfg, store, dry_run=dry_run)
        try:
            await pipeline.watch(interval=interval, max_runtime=max_runtime)
        finally:
            await pipeline.aclose()


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _missing_secrets(cfg, require_notify=not args.dry_run)
    if missing:
        return _print_missing(missing)
    try:
        asyncio.run(_watch(cfg, args.dry_run, args.interval, args.max_runtime))
    except KeyboardInterrupt:
        print("\n停止しました。")
    return 0


async def _agents(cfg: Config, dry_run: bool) -> None:
    from yosoku.agents.orchestrator import run_agents

    with open_store(cfg.store_path) as store:
        await run_agents(cfg, store, dry_run=dry_run)


def cmd_agents(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _missing_secrets(cfg, require_notify=not args.dry_run)
    if missing:
        return _print_missing(missing)
    try:
        asyncio.run(_agents(cfg, args.dry_run))
    except KeyboardInterrupt:
        print("\n停止しました。")
    return 0


# ---- sources / history / backtest ----------------------------------------


def cmd_sources(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    for src in build_sources(cfg):
        events = src.fetch()
        print(f"== {src.name}: {len(events)} 件 ==")
        for e in events[: args.limit]:
            when = f"{e.published_at:%m-%d %H:%M}" if e.published_at else "-----"
            code = e.company_code or "----"
            print(f"  [{when}] ({code}) {e.title[:60]}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    with open_store(cfg.store_path) as store:
        rows = store.recent_signals(limit=args.limit, notified_only=args.notified)
    if not rows:
        print("履歴はまだありません。")
        return 0
    for r in rows:
        flag = "🔔" if r["notified"] else "  "
        print(
            f"{flag} {r['created_at']} [{r['stage']}] {r['company_name'] or ''} "
            f"{r['ticker'] or ''} {r['direction']} score={r['score']:+d} "
            f"conf={r['confidence']} :: {r['title'][:50]}"
        )
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _missing_secrets(cfg, require_notify=False)
    if missing:
        return _print_missing(missing)
    from yosoku.backtest import run_backtest

    result = asyncio.run(
        run_backtest(
            cfg,
            start=args.start,
            end=args.end,
            horizon_days=args.horizon,
            limit=args.limit,
        )
    )
    print(result.render())
    return 0


# ---- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yosoku", description="日本株トレーディングセンサー")
    parser.add_argument("--config", "-c", help="設定 YAML のパス")
    parser.add_argument("--verbose", "-v", action="store_true", help="デバッグログ")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="1回だけ実行")
    p_run.add_argument("--dry-run", action="store_true", help="通知せず表示のみ")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="常駐してポーリング")
    p_watch.add_argument("--dry-run", action="store_true", help="通知せず表示のみ")
    p_watch.add_argument("--interval", type=int, help="ポーリング間隔(秒)")
    p_watch.add_argument(
        "--max-runtime",
        type=int,
        default=None,
        help="この秒数で綺麗に終了(GitHub Actionsの長時間ジョブ連鎖用)",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_agents = sub.add_parser("agents", help="マルチエージェント版で常駐(実験的)")
    p_agents.add_argument("--dry-run", action="store_true", help="通知せず表示のみ")
    p_agents.set_defaults(func=cmd_agents)

    p_src = sub.add_parser("sources", help="収集のみ(疎通確認)")
    p_src.add_argument("--limit", type=int, default=10, help="表示件数")
    p_src.set_defaults(func=cmd_sources)

    p_hist = sub.add_parser("history", help="過去シグナルの一覧")
    p_hist.add_argument("--limit", type=int, default=20, help="表示件数")
    p_hist.add_argument("--notified", action="store_true", help="通知済みのみ")
    p_hist.set_defaults(func=cmd_history)

    p_bt = sub.add_parser("backtest", help="過去レンジで判定品質を評価")
    p_bt.add_argument("start", help="開始日 YYYYMMDD")
    p_bt.add_argument("end", help="終了日 YYYYMMDD")
    p_bt.add_argument("--horizon", type=int, default=5, help="保有営業日数")
    p_bt.add_argument("--limit", type=int, default=200, help="取得開示の上限")
    p_bt.set_defaults(func=cmd_backtest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
