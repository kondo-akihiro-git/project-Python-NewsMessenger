#!/usr/bin/env python3
"""ニュース自動取得・AI要約・品質レビュー・Telegram通知のエントリポイント."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ai_client import ShortcutAIClient  # noqa: E402
from src.config import load_config  # noqa: E402
from src.database import ArticleStore  # noqa: E402
from src.lock import RunAlreadyActiveError, RunLock  # noqa: E402
from src.logger import setup_logger  # noqa: E402
from src.notifier import ArticleNotifier, NotificationResult  # noqa: E402
from src.pipeline import NewsPipeline, RunResult  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RSSニュースを要約・レビューし、合格記事のみTelegramへ通知する"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="今回処理する記事数の上限（省略時は config の max_articles_per_run）",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="AI処理のみ実行し、Telegram送信は行わない（検証用）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="設定・ショートカット登録・DB初期化のみ確認し、外部取得は行わない",
    )
    return parser


def print_run_result(run: RunResult) -> None:
    print(
        f"RSS新着: {run.discovered}件 / 処理: {len(run.results)}件 / "
        f"承認: {run.approved_count}件"
    )
    for result in run.results:
        flag = "通知対象" if result.approved else "通知なし"
        print(f"  [{flag}] {result.article_key} status={result.status} {result.reason}")
    for error in run.feed_errors:
        print(f"  [RSS失敗] {error}")


def print_notify_result(results: tuple[NotificationResult, ...]) -> None:
    sent = sum(1 for result in results if result.sent)
    print(f"通知送信: {sent}件 / 対象: {len(results)}件")
    for result in results:
        if not result.sent:
            print(f"  [送信失敗] {result.article_key}: {result.error}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(PROJECT_ROOT)
    extra_secrets = tuple(
        secret for secret in (config.bot_token, config.chat_id) if secret
    )
    logger = setup_logger(
        "news_telegram",
        log_file=config.log_file,
        extra_secrets=extra_secrets,
    )
    store = ArticleStore(config.database_path, logger=logger)

    if args.check_only:
        store.initialize()
        try:
            client = ShortcutAIClient(
                summarize_shortcut=config.summarize_shortcut,
                review_shortcut=config.review_shortcut,
                logger=logger,
            )
            registered = client.list_shortcuts()
        except Exception as exc:  # noqa: BLE001 - 確認モードでは結果を表示して終了
            print(f"ショートカット確認失敗: {exc}")
            return 1
        required = (config.summarize_shortcut, config.review_shortcut)
        missing = [name for name in required if name not in registered]
        print(f"登録済みショートカット: {registered}")
        print(f"必要ショートカット: {list(required)}")
        if missing:
            print(f"未登録: {missing}")
            return 1
        if not config.bot_token or not config.chat_id:
            print("Telegram認証情報が未設定です（.env を確認してください）。")
            return 1
        print("OK: 設定・ショートカット・DB初期化を確認しました。")
        return 0

    # 5分間隔の launchd 実行が重ならないよう、実行全体をファイルロックで保護する。
    lock = RunLock(config.project_root / "data" / "news.lock", logger=logger)
    try:
        with lock:
            pipeline = NewsPipeline(config, store=store, logger=logger)
            run = pipeline.run_once(limit=args.limit)
            print_run_result(run)

            if args.no_notify:
                print("--no-notify が指定されたため Telegram送信は行いません。")
                return 0

            try:
                notifier = ArticleNotifier(config, store=store, logger=logger)
            except ValueError as exc:
                logger.error(str(exc))
                print(f"通知を中止しました: {exc}")
                return 1

            results = notifier.notify_approved(limit=args.limit)
            print_notify_result(results)
            return 0
    except RunAlreadyActiveError:
        print("既に別の実行が進行中のため今回の実行をスキップしました。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
