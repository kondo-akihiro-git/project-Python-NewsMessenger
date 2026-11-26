"""RSSClient の単体テスト."""

from __future__ import annotations

import httpx

from src.config import FeedConfig
from src.rss_client import RSSClient

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title><link>https://example.com/</link><description>test</description>
  <item>
    <title>テスト記事</title>
    <link>https://example.com/news/1?utm_source=rss</link>
    <guid> Guid-1 </guid>
    <pubDate>Tue, 24 Nov 2026 10:00:00 +0900</pubDate>
    <description>概要です</description>
  </item>
  <item>
    <title>2番目の記事</title>
    <link>https://example.com/news/2</link>
  </item>
</channel></rss>
""".encode("utf-8")


def test_fetch_feed_parses_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "example.com"
        assert "NewsTelegramPersonalBot" in request.headers["User-Agent"]
        return httpx.Response(200, content=RSS_XML)

    client = RSSClient(
        timeout_seconds=5.0,
        user_agent="NewsTelegramPersonalBot/1.0",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.fetch_feed(FeedConfig(name="テスト配信", url="https://example.com/rss.xml"))
    assert result.ok is True
    assert len(result.items) == 2
    assert result.items[0].title == "テスト記事"
    assert result.items[0].guid == "Guid-1"
    assert result.items[0].source == "テスト配信"
    assert result.items[0].description == "概要です"
    assert result.items[1].link == "https://example.com/news/2"


def test_disabled_feed_does_not_call_http() -> None:
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, content=RSS_XML)

    client = RSSClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.fetch_feed(
        FeedConfig(name="停止中", url="https://example.com/rss.xml", enabled=False)
    )
    assert result.ok is True
    assert result.items == ()
    assert called == []


def test_fetch_feed_retries_and_fails_closed() -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500, text="server error")

    client = RSSClient(
        max_retries=3,
        retry_delays=(1.0, 2.0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=sleeps.append,
    )
    result = client.fetch_feed(FeedConfig(name="失敗配信", url="https://example.com/rss.xml"))
    assert result.ok is False
    assert result.items == ()
    assert "500" in (result.error or "")
    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]


def test_fetch_all_keeps_feed_order_and_isolated_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad.xml":
            return httpx.Response(404)
        return httpx.Response(200, content=RSS_XML)

    client = RSSClient(
        max_retries=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    feeds = (
        FeedConfig(name="正常", url="https://example.com/good.xml"),
        FeedConfig(name="異常", url="https://example.com/bad.xml"),
    )
    results = client.fetch_all(feeds)
    assert [r.feed_name for r in results] == ["正常", "異常"]
    assert results[0].ok is True
    assert results[1].ok is False
