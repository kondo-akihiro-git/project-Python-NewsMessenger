#!/usr/bin/env python3
"""Telegram Bot の初期セットアップ・CHAT_ID自動取得スクリプト."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import dotenv_values

from src.logger import setup_logger
from src.telegram_client import TelegramClient, TelegramError

ENV_PATH = PROJECT_ROOT / ".env"


def save_env_file(env_path: Path, bot_token: str, chat_id: str) -> None:
    """認証情報を .env ファイルに書き出し、パーミッションを 600 に制限する."""
    content = (
        "# Telegram Bot API 設定（秘密情報のため Git にコミットしないこと）\n"
        f"TELEGRAM_BOT_TOKEN={bot_token}\n"
        f"TELEGRAM_CHAT_ID={chat_id}\n"
    )
    env_path.write_text(content, encoding="utf-8")
    os.chmod(env_path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram Bot セットアップおよび疎通確認")
    parser.add_argument(
        "--send-ping",
        action="store_true",
        help="CHAT_ID 確定後に疎通確認用テストメッセージを1通送信する",
    )
    args = parser.parse_args()

    logger = setup_logger("news_telegram.setup")
    existing = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}

    bot_token = (
        os.environ.get("TELEGRAM_BOT_TOKEN")
        or str(existing.get("TELEGRAM_BOT_TOKEN") or "")
    ).strip()

    if not bot_token:
        print("TELEGRAM_BOT_TOKEN が未設定です。")
        bot_token = getpass.getpass("BotFather から取得した BOT_TOKEN を入力してください（入力は表示されません）: ").strip()
        if not bot_token:
            print("エラー: BOT_TOKEN が空のため中断します。", file=sys.stderr)
            return 1

    client = TelegramClient(bot_token=bot_token, logger=logger)

    try:
        me = client.get_me()
    except TelegramError as exc:
        print(f"Bot 認証に失敗しました: {exc}", file=sys.stderr)
        return 1

    bot_username = me.get("username", "(unknown)")
    print(f"Bot 接続成功: @{bot_username}")

    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or str(existing.get("TELEGRAM_CHAT_ID") or "")
    ).strip()

    if not chat_id:
        print("getUpdates から最新のプライベートチャットを検索しています...")
        try:
            chat_info = client.find_latest_private_chat()
        except TelegramError as exc:
            print(f"getUpdates の取得に失敗しました: {exc}", file=sys.stderr)
            return 1

        if not chat_info:
            print(
                "エラー: プライベートチャットの履歴が見つかりませんでした。\n"
                f"iPhone の Telegram で @{bot_username} を開き、「Start」を押して hello 等を送信したあと再実行してください。",
                file=sys.stderr,
            )
            return 1

        chat_id = chat_info["chat_id"]
        print(
            f"プライベートチャットを検出しました: chat_id={chat_id}, "
            f"user={chat_info['first_name']} (@{chat_info['username']}), "
            f"最新メッセージ={chat_info['text']!r}"
        )

    save_env_file(ENV_PATH, bot_token, chat_id)
    print(f".env を保存しました（権限 600）: {ENV_PATH}")

    if args.send_ping:
        result = client.send_message(
            "テスト通知です。\nニュース自動通知システムの疎通確認に成功しました。",
            chat_id=chat_id,
        )
        print(f"疎通確認メッセージを送信しました (message_id={result.message_id})。iPhone を確認してください。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
