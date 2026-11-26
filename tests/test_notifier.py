"""記事通知レイヤーの単体テスト."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.config import load_config
from src.models import APPROVED, NOTIFICATION_FAILED, NOTIFIED, REVIEW_REJECTED, FeedItem, now_iso
from src.notifier import ArticleNotifier, build_notification_text
from src.telegram_client import TelegramSendResult

BASE_CONFIG = load_config(Path(__file__).resolve().parent.parent)


class FakeTelegram:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or TelegramSendResult(
            ok=True, message_id=77, chat_id="12345"
        )
        self.error = error
        self.calls: list[dict] = []

    def send_message(self, text, chat_id=None, disable_web_page_preview=True, entities=None):
        self.calls.append(
            {
                "text": text,
                "chat_id": chat_id,
                "disable_web_page_preview": disable_web_page_preview,
                "entities": entities,
            }
        )
        if self.error:
            raise self.error
        return self.result


def make_config(tmp_path: Path):
    return replace(
        BASE_CONFIG,
        database_path=tmp_path / "news.db",
        bot_token="123456789:FAKE_TOKEN_FOR_TEST_ONLY",
        chat_id="12345",
    )


def approved_article(tmp_path: Path, status: str = APPROVED, notified_at: str = ""):
    from src.database import ArticleStore

    config = make_config(tmp_path)
    store = ArticleStore(config.database_path)
    store.initialize()
    store.discover(
        (
            FeedItem(
                title="サンプル会社が新サービスを発表　個人情報の漏えいを防止",
                link="https://example.com/news/1",
                guid="guid-notify-1",
                source="テスト配信",
            ),
        )
    )
    article = store.get("guid:guid-notify-1")
    assert article is not None
    article.summary = (
        "サンプル会社が新サービスを発表しました\n"
        "個人情報の漏えいを防ぐため、警察に相談しました"
    )
    store.update(article)
    store.set_status(
        "guid:guid-notify-1", status, review_status="PASS", notified_at=notified_at
    )
    return config, store


def test_build_notification_text_is_summary_only(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    article = store.get("guid:guid-notify-1")
    assert article is not None
    text = build_notification_text(article)
    assert text == (
        "サンプル会社が新サービスを発表しました\n"
        "個人情報の漏えいを防ぐため、警察に相談しました"
    )
    assert "元記事を読む" not in text
    assert "https://" not in text


def test_build_notification_text_rejects_long_summary(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    article = store.get("guid:guid-notify-1")
    assert article is not None
    article.summary = "とても長い要約です。" * 10
    with pytest.raises(ValueError, match="文字を超え"):
        build_notification_text(article, max_chars=55)


def test_build_notification_text_rejects_taigen_dome(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    article = store.get("guid:guid-notify-1")
    assert article is not None
    article.summary = "米マイクロソフトがコードレッドを宣言しました\n大規模障害で対応を見直し"
    with pytest.raises(ValueError, match="体言止め"):
        build_notification_text(article, max_chars=55)


def test_build_notification_text_rejects_url_in_summary(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    article = store.get("guid:guid-notify-1")
    assert article is not None
    article.summary = "要約です。\nhttps://evil.example.com"
    with pytest.raises(ValueError, match="URL"):
        build_notification_text(article)


def test_approved_article_is_sent_once_and_marked_notified(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    telegram = FakeTelegram()
    notifier = ArticleNotifier(config, store=store, telegram_client=telegram)

    results = notifier.notify_approved()
    assert len(results) == 1
    assert results[0].sent is True
    assert results[0].message_id == 77
    assert len(telegram.calls) == 1
    assert telegram.calls[0]["chat_id"] == "12345"
    assert telegram.calls[0]["text"] == (
        "サンプル会社が新サービスを発表しました\n"
        "個人情報の漏えいを防ぐため、警察に相談しました"
    )
    assert "元記事を読む" not in telegram.calls[0]["text"]
    assert "https://" not in telegram.calls[0]["text"]
    assert telegram.calls[0]["entities"] is None

    saved = store.get("guid:guid-notify-1")
    assert saved is not None
    assert saved.status == NOTIFIED
    assert saved.notified_at

    again = notifier.notify_approved()
    assert again == ()
    assert len(telegram.calls) == 1


def test_notified_article_is_not_resent(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path, notified_at=now_iso())
    telegram = FakeTelegram()
    notifier = ArticleNotifier(config, store=store, telegram_client=telegram)
    results = notifier.notify_approved()
    assert results[0].status == NOTIFIED
    assert telegram.calls == []


def test_send_failure_is_recorded_and_retried(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path)
    telegram = FakeTelegram(error=RuntimeError("network down"))
    notifier = ArticleNotifier(config, store=store, telegram_client=telegram)

    results = notifier.notify_approved()
    assert results[0].status == NOTIFICATION_FAILED
    saved = store.get("guid:guid-notify-1")
    assert saved is not None
    assert saved.status == NOTIFICATION_FAILED
    assert saved.retry_count == 1
    assert not saved.notified_at

    telegram.error = None
    retry = notifier.notify_approved()
    assert retry[0].sent is True
    final = store.get("guid:guid-notify-1")
    assert final is not None
    assert final.status == NOTIFIED


def test_rejected_article_is_never_notified(tmp_path: Path) -> None:
    config, store = approved_article(tmp_path, status=REVIEW_REJECTED)
    telegram = FakeTelegram()
    notifier = ArticleNotifier(config, store=store, telegram_client=telegram)
    assert notifier.notify_approved() == ()
    assert telegram.calls == []
