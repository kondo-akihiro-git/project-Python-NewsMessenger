"""ArticleStore の単体テスト."""

from __future__ import annotations

from pathlib import Path

from src.database import ArticleStore
from src.models import (
    DISCOVERED,
    FETCHED,
    NOTIFIED,
    REVIEW_REJECTED,
    SUMMARIZING,
    FeedItem,
)


def items() -> tuple[FeedItem, ...]:
    return (
        FeedItem(
            title="最初の記事",
            link="https://example.com/news/1",
            guid="guid-1",
            source="テスト配信",
            published_at="2026-11-25T10:00:00+09:00",
        ),
        FeedItem(
            title="2番目の記事",
            link="https://example.com/news/2",
            guid="guid-2",
            source="テスト配信",
        ),
    )


def test_discover_is_idempotent(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path / "news.db")
    store.initialize()
    assert store.discover(items()) == 2
    assert store.discover(items()) == 0
    assert store.count_by_status() == {DISCOVERED: 2}


def test_discover_normalizes_tracking_parameters(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path / "news.db")
    store.initialize()
    store.discover(
        (
            FeedItem(
                title="追跡付き",
                link="https://example.com/news/3?utm_source=rss&id=7",
                guid="guid-3",
            ),
        )
    )
    article = store.get("guid:guid-3")
    assert article is not None
    assert article.url == "https://example.com/news/3?utm_source=rss&id=7"
    assert store.get("url:https://example.com/news/3?utm_source=rss&id=7") is None


def test_update_and_status_transitions(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path / "news.db")
    store.initialize()
    store.discover(items())

    article = store.get("guid:guid-1")
    assert article is not None
    article.article_body = "本文です。" * 50
    article.summary = "要約です。"
    store.update(article)

    saved = store.get("guid:guid-1")
    assert saved is not None
    assert saved.article_body == "本文です。" * 50
    assert saved.summary == "要約です。"

    store.set_status("guid:guid-1", FETCHED, message="本文取得成功")
    store.set_status("guid:guid-1", SUMMARIZING)
    store.set_status(
        "guid:guid-1",
        NOTIFIED,
        review_status="PASS",
        notified_at="2026-11-25T12:00:00+00:00",
        increment_retry=True,
    )

    final = store.get("guid:guid-1")
    assert final is not None
    assert final.status == NOTIFIED
    assert final.review_status == "PASS"
    assert final.notified_at == "2026-11-25T12:00:00+00:00"
    assert final.retry_count == 1

    events = store.list_events("guid:guid-1")
    assert [event["to_status"] for event in events] == [
        DISCOVERED,
        FETCHED,
        SUMMARIZING,
        NOTIFIED,
    ]
    assert store.count_by_status() == {NOTIFIED: 1, DISCOVERED: 1}


def test_list_by_status_returns_oldest_first(tmp_path: Path) -> None:
    store = ArticleStore(tmp_path / "news.db")
    store.initialize()
    store.discover(items())
    store.set_status("guid:guid-2", REVIEW_REJECTED, message="根拠不明")
    found = store.list_by_status((DISCOVERED,), limit=5)
    assert [article.article_key for article in found] == ["guid:guid-1"]
    assert store.list_by_status((REVIEW_REJECTED,))[0].error_message == "根拠不明"
