#!/usr/bin/env python3
"""Phase 2: iPhone通知の見え方を検証するための Telegram テスト送信スクリプト（設計書 15-6 準拠）."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.logger import setup_logger
from src.telegram_client import TelegramClient, TelegramError


@dataclass(frozen=True)
class NotificationPattern:
    key: str
    description: str
    text: str


PATTERNS: tuple[NotificationPattern, ...] = (
    NotificationPattern(
        key="len40",
        description="40文字程度",
        text="東京都足立区の会社で、お客さんの大切な情報約5万件が外に流れ出たことが分かった。",
    ),
    NotificationPattern(
        key="len50",
        description="50文字程度",
        text="東京都足立区の会社が許可なくシステムに入られ、お客さんの情報約5万件が外に流れ出たと発表した。",
    ),
    NotificationPattern(
        key="len60",
        description="60文字程度（目標サイズ）",
        text="東京都足立区の会社は、許可なくコンピューターに入られて、お客さんの名前や住所など約5万件が外に流れ出たと発表した。",
    ),
    NotificationPattern(
        key="len65",
        description="65文字程度（目標上限）",
        text="東京都足立区のIT企業は、システムの弱い部分を攻撃されて許可なく入られ、お客さんの情報約5万件が外に流れ出たと発表した。",
    ),
    NotificationPattern(
        key="len70",
        description="70文字程度（上限目安）",
        text="東京都足立区のIT企業は、システムの弱い部分を攻撃されたことが原因で、お客さんの住所や電話番号など約5万件が外に流れ出たと発表した。",
    ),
    NotificationPattern(
        key="lines3",
        description="3行の改行あり文章",
        text=(
            "東京都足立区の会社が攻撃を受け、\n"
            "お客さんの情報約5万件が流れ出た。\n"
            "会社は仕組みを直して再発を防ぐ。"
        ),
    ),
    NotificationPattern(
        key="lines4",
        description="4行の改行あり文章（1行17〜18文字目安）",
        text=(
            "東京都足立区の会社が許可なく\n"
            "システムに入られ、お客さんの\n"
            "情報約5万件が外に流れ出た。\n"
            "会社は警察に相談し調査中。"
        ),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="設計書 15-6 に基づく Telegram 通知見え方テスト"
    )
    parser.add_argument(
        "--pattern",
        choices=[p.key for p in PATTERNS],
        help="特定のパターンのみ送信する（未指定時は全パターン送信）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="送信せずにパターン内容・文字数・行数のみ表示する",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="連続送信時の待機秒数（デフォルト: 2.0秒）",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    logger = setup_logger("news_telegram.phase2_test")

    selected = [p for p in PATTERNS if args.pattern is None or p.key == args.pattern]

    if args.dry_run:
        for idx, p in enumerate(selected, 1):
            lines = p.text.splitlines()
            print(
                f"[{idx}/{len(selected)}] {p.key} ({p.description}) | "
                f"要約文字数={len(p.text.replace(chr(10), ''))}文字 / 行数={len(lines)}行"
            )
            print(p.text)
            print("-" * 40)
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print(
            "エラー: TELEGRAM_BOT_TOKEN または TELEGRAM_CHAT_ID が設定されていません。\n"
            "先に scripts/setup_telegram.py を実行してください。",
            file=sys.stderr,
        )
        return 1

    client = TelegramClient(bot_token=bot_token, default_chat_id=chat_id, logger=logger)

    for idx, p in enumerate(selected, 1):
        char_count = len(p.text.replace("\n", ""))
        line_count = len(p.text.splitlines())
        print(
            f"[{idx}/{len(selected)}] 送信中: {p.key} ({p.description}) "
            f"[要約{char_count}文字 / {line_count}行]"
        )
        try:
            res = client.send_message(p.text, disable_web_page_preview=True)
            print(f"  -> 送信成功 (message_id={res.message_id})")
        except TelegramError as exc:
            print(f"  -> 送信失敗: {exc}", file=sys.stderr)
            return 1

        if idx < len(selected) and args.interval > 0:
            time.sleep(args.interval)

    print("\n全テストパターンの送信が完了しました。iPhone の通知センターと Telegram 画面で確認してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
