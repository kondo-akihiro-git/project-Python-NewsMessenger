#!/usr/bin/env python3
"""Phase 3: Apple Intelligence × macOSショートカット連携の実機検証スクリプト."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ai_client import AIClientError, ShortcutAIClient, render_prompt
from src.logger import setup_logger

PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"

SAMPLE_TITLE = "東京都足立区の企業で顧客情報約5万件が流出した可能性"
SAMPLE_SOURCE = "テスト配信"
SAMPLE_BODY = (
    "東京都足立区に本社を置くITサービス企業「サンプル株式会社」は25日、"
    "外部からの不正アクセスを受け、顧客の氏名やメールアドレスなど最大約5万件の"
    "個人情報が外部に流出した可能性があると発表した。"
    "同社によると、システムの脆弱性を突かれて社内の管理サーバーに侵入されたという。"
    "原因や侵入の詳しい経緯については現在調査を進めており、対象の顧客には順次連絡する。"
    "なお、警察にも相談している。"
)
BAD_SUMMARY = (
    "東京都新宿区の会社で、社員のミスにより\n"
    "顧客情報100万件が流出した。\n"
    "https://example.com/fake"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3: News_Summarize / News_Review ショートカット実機検証"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="連続実行テストの回数（デフォルト: 2回）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="ショートカットの存在確認のみ行う",
    )
    args = parser.parse_args()

    logger = setup_logger("news_telegram.phase3_test")
    client = ShortcutAIClient(logger=logger)

    print("1. 登録済みショートカットを確認中...")
    try:
        names = client.list_shortcuts()
    except AIClientError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    print(f"   登録済みショートカット: {names}")
    missing = [name for name in ("News_Summarize", "News_Review") if name not in names]
    if missing:
        print(
            f"エラー: 必要なショートカットが未作成です: {missing}\n"
            "Mac の「ショートカット」アプリで News_Summarize と News_Review を作成してください。",
            file=sys.stderr,
        )
        return 1

    if args.check_only:
        print("OK: 必要な2つのショートカットが確認できました。")
        return 0

    print("\n2. News_Summarize テスト（単発実行）...")
    summarize_prompt = render_prompt(
        PROMPTS_DIR / "summarize.txt",
        {
            "TITLE": SAMPLE_TITLE,
            "SOURCE": SAMPLE_SOURCE,
            "ARTICLE_BODY": SAMPLE_BODY,
        },
    )
    t0 = time.monotonic()
    try:
        sum_res = client.summarize(summarize_prompt)
    except AIClientError as exc:
        print(f"News_Summarize 失敗: {exc}", file=sys.stderr)
        return 1
    dt = time.monotonic() - t0
    print(f"   成功 ({dt:.2f}秒) | status={sum_res.status}")
    print(f"   summary:\n{sum_res.summary}")
    print(f"   location={sum_res.location}, numbers={sum_res.numbers}")

    print("\n3. News_Review テスト（生成した要約のレビュー）...")
    review_prompt_ok = render_prompt(
        PROMPTS_DIR / "review.txt",
        {
            "TITLE": SAMPLE_TITLE,
            "ARTICLE_BODY": SAMPLE_BODY,
            "SUMMARY": sum_res.summary,
        },
    )
    t0 = time.monotonic()
    try:
        rev_ok = client.review(review_prompt_ok)
    except AIClientError as exc:
        print(f"News_Review (正常系) 失敗: {exc}", file=sys.stderr)
        return 1
    dt = time.monotonic() - t0
    print(f"   成功 ({dt:.2f}秒) | status={rev_ok.status} | reason={rev_ok.reason}")

    print("\n4. News_Review テスト（意図的な誤要約が FAIL になるか検証）...")
    review_prompt_bad = render_prompt(
        PROMPTS_DIR / "review.txt",
        {
            "TITLE": SAMPLE_TITLE,
            "ARTICLE_BODY": SAMPLE_BODY,
            "SUMMARY": BAD_SUMMARY,
        },
    )
    t0 = time.monotonic()
    try:
        rev_bad = client.review(review_prompt_bad)
    except AIClientError as exc:
        print(f"News_Review (誤要約) 失敗: {exc}", file=sys.stderr)
        return 1
    dt = time.monotonic() - t0
    print(
        f"   成功 ({dt:.2f}秒) | status={rev_bad.status} | "
        f"unsupported={rev_bad.unsupported_claims} | reason={rev_bad.reason}"
    )
    if rev_bad.status != "FAIL":
        print("警告: 誤要約に対して PASS が返りました。プロンプト・モデルの調整が必要です。", file=sys.stderr)
        return 1

    print(f"\n5. 連続実行テスト ({args.repeat}回)...")
    for i in range(1, args.repeat + 1):
        t0 = time.monotonic()
        try:
            res = client.summarize(summarize_prompt)
            dt = time.monotonic() - t0
            print(f"   [{i}/{args.repeat}] OK ({dt:.2f}秒) | status={res.status}")
        except AIClientError as exc:
            print(f"   [{i}/{args.repeat}] FAIL: {exc}", file=sys.stderr)
            return 1

    print("\nPhase 3 実機テストがすべて正常に完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
