"""レビュー承認済み記事の Telegram 通知と冪等性制御."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.config import Config
from src.database import ArticleStore
from src.models import (
    APPROVED,
    NOTIFICATION_FAILED,
    NOTIFIED,
    Article,
    now_iso,
)
from src.telegram_client import TelegramClient
from src.text_quality import has_taigen_dome, missing_required_elements

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_MAX_NOTIFY_RETRIES = 3
_MAX_SUMMARY_LINES = 3
_LINK_LABEL = "元記事を読む"


def _utf16_len(text: str) -> int:
    """Telegram の offset/length は UTF-16 コード単位で指定する."""
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class NotificationResult:
    """記事1件の通知結果."""

    article_key: str
    status: str
    message_id: int | None = None
    error: str = ""

    @property
    def sent(self) -> bool:
        return self.status == NOTIFIED


def build_notification_text(article: Article, max_chars: int = 55) -> str:
    """要約本文（URLなし）＋リンク文言の短い通知テキストを生成する.

    URL 本文を入れず `text_link` entity でリンクを付けるため、通知プレビューで
    要約が省略されない。要約が空・URL混入・行数または文字数超過の場合は通知しない。
    """
    summary = (article.summary or "").strip()
    if not summary:
        raise ValueError("要約が空のため通知しません。")
    if _URL_PATTERN.search(summary):
        raise ValueError("要約本文にURLが含まれるため通知しません。")
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    if not lines or len(lines) > _MAX_SUMMARY_LINES:
        raise ValueError(f"要約が {_MAX_SUMMARY_LINES} 行以内でないため通知しません。")
    normalized = "\n".join(lines)
    if len(normalized) > max_chars:
        raise ValueError(
            f"要約が {max_chars} 文字を超えているため通知しません ({len(normalized)}文字)。"
        )
    if has_taigen_dome(normalized):
        raise ValueError("要約に体言止めが含まれるため通知しません。")
    missing = missing_required_elements(normalized, article.title or "")
    if missing:
        raise ValueError(
            "要約に必須要素が不足しているため通知しません: " + "、".join(missing)
        )
    if not (article.url or "").strip():
        raise ValueError("元記事URLが空のため通知しません。")
    return f"{normalized}\n\n{_LINK_LABEL}"


def build_link_entity(text: str, url: str) -> dict[str, Any]:
    """通知テキスト末尾のリンク文言に text_link を付与する."""
    length = _utf16_len(_LINK_LABEL)
    offset = _utf16_len(text) - length
    return {
        "type": "text_link",
        "offset": offset,
        "length": length,
        "url": url,
    }


class ArticleNotifier:
    """APPROVED 記事のみを Telegram へ送信し、送信結果をDBへ反映する."""

    def __init__(
        self,
        config: Config,
        *,
        store: ArticleStore | None = None,
        telegram_client: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("news_telegram.notifier")
        self.store = store or ArticleStore(config.database_path, logger=self.logger)
        if telegram_client is not None:
            self.telegram = telegram_client
        else:
            if not config.bot_token or not config.chat_id:
                raise ValueError(
                    "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が未設定のため通知できません。"
                )
            self.telegram = TelegramClient(
                config.bot_token,
                config.chat_id,
                retry_delays=config.telegram_retry_delays_seconds,
                logger=self.logger,
            )

    def notify_approved(self, limit: int | None = None) -> tuple[NotificationResult, ...]:
        """承認済み（または通知失敗）記事を順番に送信する."""
        count = self.config.max_articles_per_run if limit is None else limit
        targets = self.store.list_by_status((APPROVED, NOTIFICATION_FAILED), limit=count)
        results: list[NotificationResult] = []
        for article in targets:
            results.append(self._notify_one(article))
        return tuple(results)

    def _notify_one(self, article: Article) -> NotificationResult:
        key = article.article_key
        # 二重送信防止: 送信済みタイムスタンプがある場合は再送しない。
        if article.notified_at:
            return NotificationResult(article_key=key, status=NOTIFIED)
        if article.retry_count >= _MAX_NOTIFY_RETRIES:
            message = "通知リトライ上限に達したため送信しません。"
            self.store.set_status(key, NOTIFICATION_FAILED, message=message)
            return NotificationResult(article_key=key, status=NOTIFICATION_FAILED, error=message)

        try:
            text = build_notification_text(
                article, max_chars=self.config.max_summary_chars
            )
        except ValueError as exc:
            message = str(exc)
            self.store.set_status(
                key, NOTIFICATION_FAILED, message=message, increment_retry=True
            )
            self.logger.warning("通知をスキップ: %s (%s)", key, message)
            return NotificationResult(
                article_key=key, status=NOTIFICATION_FAILED, error=message
            )

        try:
            sent = self.telegram.send_message(
                text,
                chat_id=self.config.chat_id,
                disable_web_page_preview=self.config.telegram_disable_web_page_preview,
                entities=[build_link_entity(text, article.url)],
            )
        except Exception as exc:  # noqa: BLE001 - 送信失敗は状態に記録して継続
            message = f"Telegram送信失敗: {type(exc).__name__}: {exc}"
            self.store.set_status(
                key, NOTIFICATION_FAILED, message=message, increment_retry=True
            )
            self.logger.error(message)
            return NotificationResult(
                article_key=key, status=NOTIFICATION_FAILED, error=message
            )

        if not getattr(sent, "ok", False):
            message = "Telegram送信結果が ok=False のため通知成功とみなしません。"
            self.store.set_status(
                key, NOTIFICATION_FAILED, message=message, increment_retry=True
            )
            return NotificationResult(
                article_key=key, status=NOTIFICATION_FAILED, error=message
            )

        self.store.set_status(
            key,
            NOTIFIED,
            notified_at=now_iso(),
            message="通知送信完了",
        )
        return NotificationResult(
            article_key=key,
            status=NOTIFIED,
            message_id=getattr(sent, "message_id", None),
        )
