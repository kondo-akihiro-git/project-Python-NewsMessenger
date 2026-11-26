"""RSSフィードの取得と FeedItem への変換."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import feedparser
import httpx

from src.config import FeedConfig
from src.models import FeedItem


class RSSFetchError(Exception):
    """RSS取得・解析に失敗した場合のエラー."""


@dataclass(frozen=True)
class FeedFetchResult:
    """1つのフィードの取得結果."""

    feed_name: str
    url: str
    items: tuple[FeedItem, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class RSSClient:
    """RSSを取得して FeedItem のリストを返すクライアント."""

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        user_agent: str = "NewsTelegramPersonalBot/1.0",
        max_retries: int = 3,
        retry_delays: Sequence[float] = (1.0, 3.0, 10.0),
        http_client: httpx.Client | None = None,
        logger: logging.Logger | None = None,
        sleep_fn: Any = None,
    ) -> None:
        self._timeout = float(timeout_seconds)
        self._user_agent = user_agent
        self._max_retries = max(1, int(max_retries))
        self._retry_delays = tuple(float(v) for v in retry_delays)
        self._client = http_client
        self._owns_client = http_client is None
        self._logger = logger or logging.getLogger("news_telegram.rss")
        import time

        self._sleep = sleep_fn or time.sleep

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, follow_redirects=True)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def fetch_feed(self, feed: FeedConfig) -> FeedFetchResult:
        """1つのフィードを取得して解析する. 失敗しても例外を外に出さない."""
        if not feed.enabled:
            return FeedFetchResult(feed_name=feed.name, url=feed.url)
        if not feed.url:
            return FeedFetchResult(
                feed_name=feed.name,
                url=feed.url,
                error="フィードURLが設定されていません。",
            )

        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._get_client().get(
                    feed.url,
                    headers={"User-Agent": self._user_agent, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                    timeout=self._timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return self._parse(feed, response.content)
            except Exception as exc:  # noqa: BLE001 - 取得失敗は結果に変換してフェイルクローズ
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    delay = self._retry_delays[min(attempt - 1, len(self._retry_delays) - 1)] if self._retry_delays else 0.0
                    self._logger.warning(
                        "RSS取得失敗 (%s 試行 %d/%d): %s",
                        feed.name,
                        attempt,
                        self._max_retries,
                        last_error,
                    )
                    if delay:
                        self._sleep(delay)

        self._logger.error("RSS取得を断念: %s (%s)", feed.name, last_error)
        return FeedFetchResult(feed_name=feed.name, url=feed.url, error=last_error)

    def _parse(self, feed: FeedConfig, content: bytes) -> FeedFetchResult:
        parsed = feedparser.parse(content)
        if parsed.bozo and not parsed.entries:
            raise RSSFetchError(f"RSS解析失敗: {parsed.bozo_exception}")
        if not parsed.entries:
            raise RSSFetchError("RSSに記事が含まれていません。")

        items = []
        for entry in parsed.entries:
            link = str(entry.get("link") or "").strip()
            title = str(entry.get("title") or "").strip()
            if not link or not title:
                continue
            guid = str(entry.get("guid") or entry.get("id") or "").strip()
            published = str(entry.get("published") or entry.get("updated") or "").strip()
            description = str(
                entry.get("description") or entry.get("summary") or ""
            ).strip()
            items.append(
                FeedItem(
                    title=title,
                    link=link,
                    published_at=published,
                    source=feed.name,
                    description=description,
                    guid=guid,
                )
            )

        if not items:
            raise RSSFetchError("有効な記事項目がありません。")
        return FeedFetchResult(feed_name=feed.name, url=feed.url, items=tuple(items))

    def fetch_all(self, feeds: Sequence[FeedConfig]) -> tuple[FeedFetchResult, ...]:
        """複数のフィードを順番に取得する（同時処理数1の原則を守る）."""
        return tuple(self.fetch_feed(feed) for feed in feeds)
