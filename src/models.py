"""ニュース記事・RSS項目のデータモデルと識別子の正規化."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 処理状態（設計書 3-3）
DISCOVERED = "DISCOVERED"
FETCHING = "FETCHING"
FETCHED = "FETCHED"
SUMMARIZING = "SUMMARIZING"
REVIEWING = "REVIEWING"
APPROVED = "APPROVED"
NOTIFIED = "NOTIFIED"

# 例外状態
FETCH_FAILED = "FETCH_FAILED"
INSUFFICIENT_CONTENT = "INSUFFICIENT_CONTENT"
SUMMARY_FAILED = "SUMMARY_FAILED"
REVIEW_FAILED = "REVIEW_FAILED"
REVIEW_REJECTED = "REVIEW_REJECTED"
NOTIFICATION_FAILED = "NOTIFICATION_FAILED"

# 追跡用パラメータ（記事識別に不要なものだけを除去）
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "igshid",
    "ref",
    "spm",
    "_r",
}


def now_iso() -> str:
    """UTCのISO8601タイムスタンプを返す."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    """記事URLを正規化する（追跡パラメータとフラグメントを除去）."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    host = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def make_article_key(item: "FeedItem") -> str:
    """RSS GUID / 正規化URL / URL+見出し の順で一意識別子を作る."""
    if item.guid and item.guid.strip():
        return f"guid:{item.guid.strip()}"
    normalized = normalize_url(item.link)
    if normalized:
        return f"url:{normalized}"
    digest = hashlib.sha256(f"{item.link}|{item.title}".encode("utf-8")).hexdigest()
    return f"hash:{digest}"


@dataclass(frozen=True)
class FeedItem:
    """RSSから取得した1件のニュース."""

    title: str
    link: str
    published_at: str = ""
    source: str = ""
    description: str = ""
    guid: str = ""


@dataclass
class Article:
    """SQLiteに保存する記事エンティティ."""

    article_key: str
    title: str
    url: str
    source: str = ""
    published_at: str = ""
    discovered_at: str = field(default_factory=now_iso)
    article_body: str = ""
    summary: str = ""
    status: str = DISCOVERED
    review_status: str = ""
    notified_at: str = ""
    retry_count: int = 0
    error_message: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_feed_item(cls, item: FeedItem) -> "Article":
        """RSS項目からArticleを生成する."""
        key = make_article_key(item)
        timestamp = now_iso()
        return cls(
            article_key=key,
            title=item.title.strip(),
            url=item.link.strip(),
            source=item.source.strip(),
            published_at=item.published_at.strip(),
            discovered_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
