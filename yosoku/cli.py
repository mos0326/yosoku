"""コマンドラインインターフェース.

  python -m yosoku run                 # 1回だけ収集→分析→通知
  python -m yosoku run --dry-run       # 通知せずシグナルを表示
  python -m yosoku watch --interval 300  # 常駐してポーリング
  python -m yosoku sources             # 収集だけ試す(分析・通知なし)
"""

from __future__ import annotations

import argparse
import logging
import sys

from yosoku.config import load_config
from yosoku.pipeline import build_pipeline, build_sources
from yosoku.store import open_store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _check_secrets(cfg, require_notify: bool) -> list[str]:
    """不足しているシークレットの説明リストを返す。"""
    missing = []
    if not cfg.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY(分析に必須)")
    if require_notify and not cfg.discord_webhook_url:
        missing.append("DISCORD_WEBHOOK_URL(通知に必須。--dry-run なら不要)")
    return missing


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _check_secrets(cfg, require_notify=not args.dry_run)
    if missing:
        print("環境変数が不足しています:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2
    with open_store(cfg.store_path) as store:
        pipeline = build_pipeline(cfg, store, dry_run=args.dry_run)
        result = pipeline.run_once()
    print(
        f"collected={result.collected} new={result.new} "
        f"analyzed={result.analyzed} notified={result.notified}"
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = _check_secrets(cfg, require_notify=not args.dry_run)
    if missing:
        print("環境変数が不足しています:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2
    with open_store(cfg.store_path) as store:
        pipeline = build_pipeline(cfg, store, dry_run=args.dry_run)
        try:
            pipeline.watch(interval=args.interval)
        except KeyboardInterrupt:
            print("\n停止しました。")
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    """分析・通知なしで、収集だけを試す(疎通確認用)。"""
    cfg = load_config(args.config)
    sources = build_sources(cfg)
    for src in sources:
        events = src.fetch()
        print(f"== {src.name}: {len(events)} 件 ==")
        for e in events[: args.limit]:
            when = f"{e.published_at:%m-%d %H:%M}" if e.published_at else "-----"
            code = e.company_code or "----"
            print(f"  [{when}] ({code}) {e.title[:60]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yosoku", description="日本株トレーディングセンサー"
    )
    parser.add_argument("--config", "-c", help="設定 YAML のパス")
    parser.add_argument("--verbose", "-v", action="store_true", help="デバッグログ")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="1回だけ実行")
    p_run.add_argument("--dry-run", action="store_true", help="通知せず表示のみ")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="常駐してポーリング")
    p_watch.add_argument("--dry-run", action="store_true", help="通知せず表示のみ")
    p_watch.add_argument("--interval", type=int, help="ポーリング間隔(秒)")
    p_watch.set_defaults(func=cmd_watch)

    p_src = sub.add_parser("sources", help="収集のみ(疎通確認)")
    p_src.add_argument("--limit", type=int, default=10, help="表示件数")
    p_src.set_defaults(func=cmd_sources)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
