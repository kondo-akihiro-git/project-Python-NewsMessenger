"""記事1件の要約・レビュー・修正ループと、1実行分のオーケストレーション."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from src.ai_client import (
    AIClientError,
    ReviewResult,
    OnDeviceAIClient,
    ShortcutAIClient,
    SummaryResult,
    render_prompt,
)
from src.config import Config
from src.content_extractor import ContentExtractor
from src.database import ArticleStore
from src.text_quality import missing_required_elements, trim_related_articles
from src.models import (
    APPROVED,
    DISCOVERED,
    FETCHED,
    FETCH_FAILED,
    FETCHING,
    INSUFFICIENT_CONTENT,
    REVIEW_FAILED,
    REVIEW_REJECTED,
    REVIEWING,
    SUMMARY_FAILED,
    SUMMARIZING,
    Article,
)
from src.rss_client import RSSClient

# オンデバイス Foundation Models のコンテキスト窓は4096トークン。
# ガイド付きJSON生成ではスキーマ分のオーバーヘッドがあり、実測で
# 要約プロンプト全体は約3100文字が上限（それ以上で ContextLengthError）。
# プロンプト本体（要約2557文字）と合わせて超過しないよう、
# AIへ渡す本文は記事のリード文と用語説明を含む先頭450文字までに制限する。
# 記事の重要な事実は冒頭に集中しており、後半の関連話題は要約に不要。
_MAX_AI_BODY_CHARS = 450


def _limited_body(text: str) -> str:
    """関連記事を除き、文の途中で切れないよう本文を短くする。"""
    body = trim_related_articles(text)
    if len(body) <= _MAX_AI_BODY_CHARS:
        return body
    cut = body[:_MAX_AI_BODY_CHARS]
    boundary = max(cut.rfind("。"), cut.rfind("\n"))
    if boundary >= _MAX_AI_BODY_CHARS // 2:
        cut = cut[: boundary + 1]
    return cut.strip()


@dataclass(frozen=True)
class PipelineResult:
    """記事1件の処理結果."""

    article_key: str
    status: str
    summary: str = ""
    review_status: str = ""
    reason: str = ""
    review_attempts: int = 0
    error: str = ""

    @property
    def approved(self) -> bool:
        return self.status == APPROVED


@dataclass(frozen=True)
class RunResult:
    """1回の実行（RSS検出 + 記事処理）の結果."""

    discovered: int
    results: tuple[PipelineResult, ...]
    feed_errors: tuple[str, ...] = ()

    @property
    def approved_count(self) -> int:
        return sum(1 for result in self.results if result.approved)


class NewsPipeline:
    """RSS検出からレビュー承認までを記事単位で実行する."""

    def __init__(
        self,
        config: Config,
        *,
        store: ArticleStore | None = None,
        rss_client: RSSClient | None = None,
        extractor: ContentExtractor | None = None,
        ai_client: Any = None,
        prompts_dir: Path | str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("news_telegram.pipeline")
        self.store = store or ArticleStore(config.database_path, logger=self.logger)
        self.rss = rss_client or RSSClient(
            timeout_seconds=config.http_timeout_seconds,
            user_agent=config.user_agent,
            max_retries=config.http_max_retries,
            retry_delays=config.http_retry_delays_seconds,
        )
        self.extractor = extractor or ContentExtractor(
            timeout_seconds=config.http_timeout_seconds,
            user_agent=config.user_agent,
            min_chars_guideline=config.content_min_chars_guideline,
        )
        # 既定はオンデバイスの Apple Intelligence（Cloud は利用制限で失敗しやすいため）。
        # ショートカット経由が必要な場合は ai_client を明示的に渡す。
        self.ai = ai_client or OnDeviceAIClient(
            timeout_seconds=config.ai_timeout_seconds,
        )
        self.prompts_dir = (
            Path(prompts_dir)
            if prompts_dir is not None
            else config.project_root / "config" / "prompts"
        )

    def _prompt(self, name: str, variables: dict[str, str]) -> str:
        # 関連記事一覧は別記事の事実であり、要約・レビューの根拠にしてはならない。
        # プロンプトへ渡す直前に本文だけを残す（DB の保存内容は変更しない）。
        cleaned = dict(variables)
        if "ARTICLE_BODY" in cleaned:
            cleaned["ARTICLE_BODY"] = _limited_body(cleaned["ARTICLE_BODY"])
        path = self.prompts_dir / f"{name}.txt"
        return render_prompt(path, cleaned)

    def discover_new(self) -> tuple[int, tuple[str, ...]]:
        """RSSを取得して新着記事をDBに登録する."""
        results = self.rss.fetch_all(self.config.feeds)
        items = tuple(item for result in results for item in result.items)
        errors = tuple(
            f"{result.feed_name}: {result.error}"
            for result in results
            if result.error
        )
        inserted = self.store.discover(items)
        self.logger.info(
            "RSS検出: 新着 %d件 / 取得 %d件 / 失敗フィード %d件",
            inserted,
            len(items),
            len(errors),
        )
        return inserted, errors

    def _fetch_body(self, article: Article) -> tuple[bool, str]:
        """記事本文を取得して保存する. (成功可否, メッセージ) を返す."""
        if article.article_body.strip():
            return True, "既存の本文を使用"
        self.store.set_status(article.article_key, FETCHING)
        content = self.extractor.fetch(article.url)
        if not content.ok:
            message = content.error or f"本文が{min(100, content.char_count)}文字未満です"
            status = FETCH_FAILED if content.error else INSUFFICIENT_CONTENT
            self.store.set_status(article.article_key, status, message=message)
            return False, message
        article.article_body = content.text
        self.store.update(article)
        self.store.set_status(
            article.article_key, FETCHED, message=f"本文 {content.char_count}文字を取得"
        )
        return True, f"本文 {content.char_count}文字"

    def _summarize(self, article: Article) -> tuple[SummaryResult | None, str]:
        """要約を実行する. (要約, エラーメッセージ) を返す."""
        self.store.set_status(article.article_key, SUMMARIZING)
        prompt_variables = {
            "TITLE": article.title,
            "SOURCE": article.source or "不明",
            "ARTICLE_BODY": article.article_body,
        }
        prompt = self._prompt("summarize", prompt_variables)
        # 再生成は元プロンプトへ長文を追記すると無視されやすいため、
        # 事実（記事本文）を残した短い専用プロンプトを使う。
        retry_template = self._prompt("shorten", prompt_variables)
        try:
            summary = self.ai.summarize(
                prompt,
                max_lines=self.config.max_summary_lines,
                max_chars=self.config.max_summary_chars,
                retry_template=retry_template,
            )
        except AIClientError as exc:
            message = f"要約生成に失敗: {exc}"
            self.store.set_status(
                article.article_key, SUMMARY_FAILED, message=message, increment_retry=True
            )
            return None, message

        if summary.status != "OK" or not summary.summary:
            message = "AIが情報不足と判定したため通知しません。"
            self.store.set_status(
                article.article_key, INSUFFICIENT_CONTENT, message=message
            )
            return None, message
        missing = missing_required_elements(summary.summary, article.title)
        if missing:
            message = "要約に必須要素が不足しているため通知しません: " + "、".join(missing)
            self.store.set_status(
                article.article_key, INSUFFICIENT_CONTENT, message=message
            )
            return None, message
        return summary, ""

    def _review(self, article: Article, summary: SummaryResult) -> tuple[ReviewResult | None, str]:
        """レビューを実行する. (レビュー結果, エラーメッセージ) を返す."""
        self.store.set_status(article.article_key, REVIEWING, review_status="")
        prompt = self._prompt(
            "review",
            {"ARTICLE_BODY": article.article_body, "SUMMARY": summary.summary},
        )
        try:
            review = self.ai.review(prompt)
        except AIClientError as exc:
            message = f"レビュー実行に失敗: {exc}"
            self.store.set_status(
                article.article_key, REVIEW_FAILED, message=message, increment_retry=True
            )
            return None, message
        return review, ""

    def _revise(
        self,
        article: Article,
        review: ReviewResult,
        previous_summary: str,
    ) -> tuple[SummaryResult | None, str]:
        """レビュー指摘を反映した修正要約を生成する."""
        prompt = self._prompt(
            "revise",
            {
                "TITLE": article.title,
                "SOURCE": article.source or "不明",
                "ARTICLE_BODY": article.article_body,
                "PREVIOUS_SUMMARY": previous_summary,
                "REVIEW_REASON": review.reason,
                "ISSUES": "、".join(review.issues) or "なし",
                "UNSUPPORTED_CLAIMS": "、".join(review.unsupported_claims) or "なし",
                "MISSING_FACTS": "、".join(review.missing_facts) or "なし",
                "CORRECTIONS": "、".join(review.corrections) or "なし",
                "UNEXPLAINED_TERMS": "、".join(review.unexplained_terms) or "なし",
                "MISSING_4W1H": "、".join(review.missing_4w1h) or "なし",
                "FOUR_W_ONE_H_OK": "OK" if review.four_w_one_h_ok else "不足",
                "CONCRETE_BASIS_OK": "OK" if review.concrete_basis_ok else "根拠なし",
                "SINGLE_EVENT_OK": "OK" if review.single_event_ok else "複数イベント",
            },
        )
        try:
            revised = self.ai.revise(
                prompt,
                max_lines=self.config.max_summary_lines,
                max_chars=self.config.max_summary_chars,
            )
        except AIClientError as exc:
            return None, f"修正要約に失敗: {exc}"
        if revised.status != "OK" or not revised.summary:
            return None, "修正後も正確な要約が作れないため通知しません。"
        missing = missing_required_elements(revised.summary, article.title)
        if missing:
            return (
                None,
                "修正後も必須要素が不足しているため通知しません: " + "、".join(missing),
            )
        return revised, ""

    def process_one(self, article: Article) -> PipelineResult:
        """記事1件を処理する. 例外を外に出さず、必ずいずれかの状態で終了する."""
        key = article.article_key
        ok, message = self._fetch_body(article)
        if not ok:
            return PipelineResult(
                article_key=key,
                status=self.store.get(key).status,
                reason=message,
                error=message,
            )

        summary, error = self._summarize(article)
        if summary is None:
            current = self.store.get(key)
            return PipelineResult(
                article_key=key, status=current.status if current else SUMMARY_FAILED, reason=error, error=error
            )

        review_attempts = 0
        revisions = 0
        current_summary = summary
        while True:
            review_attempts += 1
            review, error = self._review(article, current_summary)
            if review is None:
                current = self.store.get(key)
                return PipelineResult(
                    article_key=key,
                    status=current.status if current else REVIEW_FAILED,
                    summary=current_summary.summary,
                    review_status="",
                    reason=error,
                    review_attempts=review_attempts,
                    error=error,
                )

            if review.status == "PASS":
                article.summary = current_summary.summary
                self.store.update(article)
                self.store.set_status(
                    key, APPROVED, review_status="PASS", message=review.reason
                )
                return PipelineResult(
                    article_key=key,
                    status=APPROVED,
                    summary=current_summary.summary,
                    review_status="PASS",
                    reason=review.reason,
                    review_attempts=review_attempts,
                )

            if revisions >= self.config.max_revision_attempts:
                message = f"レビュー不合格: {review.reason}"
                self.store.set_status(
                    key, REVIEW_REJECTED, review_status="FAIL", message=message
                )
                return PipelineResult(
                    article_key=key,
                    status=REVIEW_REJECTED,
                    summary=current_summary.summary,
                    review_status="FAIL",
                    reason=message,
                    review_attempts=review_attempts,
                    error=message,
                )

            revised, error = self._revise(article, review, current_summary.summary)
            revisions += 1
            if revised is None:
                self.store.set_status(
                    key, REVIEW_REJECTED, review_status="FAIL", message=error
                )
                return PipelineResult(
                    article_key=key,
                    status=REVIEW_REJECTED,
                    summary=current_summary.summary,
                    review_status="FAIL",
                    reason=error,
                    review_attempts=review_attempts,
                    error=error,
                )
            current_summary = revised

    def process_pending(self, limit: int | None = None) -> tuple[PipelineResult, ...]:
        """未処理記事を順番に処理する."""
        count = self.config.max_articles_per_run if limit is None else limit
        targets: Sequence[Article] = self.store.list_by_status((DISCOVERED,), limit=count)
        self.logger.info("処理対象: %d件", len(targets))
        return tuple(self.process_one(article) for article in targets)

    def run_once(self, limit: int | None = None) -> RunResult:
        """RSS検出 → 未処理記事の要約・レビューまでを1回実行する."""
        self.store.initialize()
        discovered, errors = self.discover_new()
        results = self.process_pending(limit=limit)
        return RunResult(discovered=discovered, results=results, feed_errors=errors)
