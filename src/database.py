"""記事・処理状態・イベントの SQLite 永続化."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from src.models import (
    DISCOVERED,
    Article,
    FeedItem,
    now_iso,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_key   TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    published_at  TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL,
    article_body  TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT '',
    notified_at   TEXT NOT NULL DEFAULT '',
    retry_count   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_at);
CREATE INDEX IF NOT EXISTS idx_articles_notified ON articles(notified_at);

CREATE TABLE IF NOT EXISTS article_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_key TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status   TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (article_key) REFERENCES articles(article_key)
);
CREATE INDEX IF NOT EXISTS idx_events_article ON article_events(article_key);
"""

_ARTICLE_COLUMNS = (
    "article_key",
    "title",
    "url",
    "source",
    "published_at",
    "discovered_at",
    "article_body",
    "summary",
    "status",
    "review_status",
    "notified_at",
    "retry_count",
    "error_message",
    "created_at",
    "updated_at",
)


class ArticleStore:
    """SQLite に対する記事の保存・状態更新を担う."""

    def __init__(self, db_path: Path | str, logger: logging.Logger | None = None) -> None:
        self.db_path = Path(db_path).resolve()
        self._logger = logger or logging.getLogger("news_telegram.database")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> Article:
        return Article(**{key: row[key] for key in _ARTICLE_COLUMNS})

    def discover(self, items: Sequence[FeedItem]) -> int:
        """RSS項目を新規登録し、新しく追加された件数を返す（冪等）."""
        inserted = 0
        with self.connect() as conn:
            for item in items:
                article = Article.from_feed_item(item)
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO articles "
                    "(article_key, title, url, source, published_at, discovered_at, "
                    " article_body, summary, status, review_status, notified_at, "
                    " retry_count, error_message, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(getattr(article, key) for key in _ARTICLE_COLUMNS),
                )
                if cursor.rowcount > 0:
                    inserted += 1
                    self._record_event(
                        conn, article.article_key, "", DISCOVERED, "RSSで新着を検出"
                    )
        return inserted

    def get(self, article_key: str) -> Article | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE article_key = ?", (article_key,)
            ).fetchone()
            return self._row_to_article(row) if row else None

    def update(self, article: Article) -> None:
        """Article の全フィールドを保存する（updated_at は現在時刻）."""
        article.updated_at = now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE articles SET "
                + ", ".join(f"{key} = ?" for key in _ARTICLE_COLUMNS if key != "article_key")
                + " WHERE article_key = ?",
                tuple(getattr(article, key) for key in _ARTICLE_COLUMNS if key != "article_key")
                + (article.article_key,),
            )

    def set_status(
        self,
        article_key: str,
        status: str,
        *,
        message: str = "",
        review_status: str | None = None,
        notified_at: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        """状態を遷移させ、イベントログを残す."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM articles WHERE article_key = ?", (article_key,)
            ).fetchone()
            if row is None:
                self._logger.warning("状態更新対象が存在しません: %s", article_key)
                return
            from_status = row["status"]
            assignments = ["status = ?", "updated_at = ?"]
            values: list = [status, now_iso()]
            assignments.append("error_message = ?")
            values.append(message)
            if review_status is not None:
                assignments.append("review_status = ?")
                values.append(review_status)
            if notified_at is not None:
                assignments.append("notified_at = ?")
                values.append(notified_at)
            if increment_retry:
                assignments.append("retry_count = retry_count + 1")
            values.append(article_key)
            conn.execute(
                f"UPDATE articles SET {', '.join(assignments)} WHERE article_key = ?",
                tuple(values),
            )
            self._record_event(conn, article_key, from_status, status, message)

    @staticmethod
    def _record_event(
        conn: sqlite3.Connection,
        article_key: str,
        from_status: str,
        to_status: str,
        message: str,
    ) -> None:
        conn.execute(
            "INSERT INTO article_events "
            "(article_key, from_status, to_status, message, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (article_key, from_status, to_status, message, now_iso()),
        )

    def list_by_status(self, statuses: Sequence[str], limit: int = 10) -> list[Article]:
        """指定状態の記事を古い順に取得する."""
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM articles WHERE status IN ({placeholders}) "
                "ORDER BY discovered_at ASC LIMIT ?",
                tuple(statuses) + (max(1, int(limit)),),
            ).fetchall()
            return [self._row_to_article(row) for row in rows]

    def count_by_status(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM articles GROUP BY status"
            ).fetchall()
            return {row["status"]: int(row["count"]) for row in rows}

    def list_events(self, article_key: str, limit: int = 50) -> list[dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT from_status, to_status, message, created_at FROM article_events "
                "WHERE article_key = ? ORDER BY id ASC LIMIT ?",
                (article_key, max(1, int(limit))),
            ).fetchall()
            return [dict(row) for row in rows]
