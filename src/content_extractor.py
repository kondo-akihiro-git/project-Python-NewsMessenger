"""記事HTMLの取得と本文抽出（readability などの外部ライブラリに依存しない）."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
from bs4 import BeautifulSoup

_REMOVE_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "iframe",
    "svg",
    "button",
    "select",
    "template",
)
_PRIMARY_SELECTORS = (
    "article",
    '[itemprop="articleBody"]',
    '[role="article"]',
    ".article-body",
    ".articleBody",
    "#article-body",
    ".news-article__body",
    ".entry-content",
    ".post-content",
    "main",
)
_TEXT_TAGS = ("p", "li")
_WS_RE = re.compile(r"[\u00a0\u3000\s]+")
_MIN_LINE_CHARS = 8


class ContentFetchError(Exception):
    """記事HTML取得・本文抽出の失敗."""


@dataclass(frozen=True)
class ArticleContent:
    """抽出された記事本文と十分性の判定."""

    url: str
    text: str = ""
    char_count: int = 0
    is_sufficient: bool = False
    meets_guideline: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        """取得成功かつ最低限の本文がある場合のみ True."""
        return not self.error and self.is_sufficient


class ContentExtractor:
    """記事ページから本文テキストを抽出する."""

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        user_agent: str = "NewsTelegramPersonalBot/1.0",
        min_chars_guideline: int = 400,
        min_absolute_chars: int | None = None,
        http_client: httpx.Client | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._timeout = float(timeout_seconds)
        self._user_agent = user_agent
        self._min_guideline = max(1, int(min_chars_guideline))
        # 目安値の1/4を最低限の絶対フロアとする（400文字設定なら100文字）。
        self._min_absolute = (
            max(80, self._min_guideline // 4)
            if min_absolute_chars is None
            else max(1, int(min_absolute_chars))
        )
        self._client = http_client
        self._owns_client = http_client is None
        self._logger = logger or logging.getLogger("news_telegram.content")

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, follow_redirects=True)
        return self._client

    def fetch(self, url: str) -> ArticleContent:
        """URLからHTMLを取得して本文を抽出する. 失敗時は error を設定."""
        if not url:
            return ArticleContent(url=url, error="記事URLが空です。")
        try:
            response = self._get_client().get(
                url,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=self._timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - 取得失敗は結果に変換してフェイルクローズ
            message = f"{type(exc).__name__}: {exc}"
            self._logger.warning("記事本文取得失敗: %s (%s)", url, message)
            return ArticleContent(url=url, error=message)
        return self.extract(response.text, url)

    def extract(self, html: str, url: str = "") -> ArticleContent:
        """HTML文字列から本文を抽出し、十分性を判定する."""
        if not html or not html.strip():
            return ArticleContent(url=url, error="HTMLが空です。")

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(list(_REMOVE_TAGS)):
            tag.decompose()

        container = self._pick_container(soup)
        if container is None:
            return ArticleContent(url=url, error="本文らしい要素が見つかりません。")

        text = self._extract_text(container)
        char_count = len(_WS_RE.sub("", text))
        return ArticleContent(
            url=url,
            text=text,
            char_count=char_count,
            is_sufficient=char_count >= self._min_absolute,
            meets_guideline=char_count >= self._min_guideline,
        )

    def _pick_container(self, soup: BeautifulSoup):
        """本文を含む可能性の高い要素を、テキスト量が多い順に選ぶ."""
        candidates = []
        for selector in _PRIMARY_SELECTORS:
            try:
                candidates.extend(soup.select(selector))
            except Exception:  # noqa: BLE001 - 不正なセレクタは無視
                continue
        best = None
        best_len = -1
        for node in candidates:
            length = len(_WS_RE.sub("", node.get_text(" ", strip=True)))
            if length > best_len:
                best, best_len = node, length
        if best is not None and best_len > 0:
            return best
        return soup.body or soup

    def _extract_text(self, container) -> str:
        """段落・リスト項目を中心に、改行を保った本文テキストを返す."""
        lines: list[str] = []
        for tag in container.find_all(_TEXT_TAGS):
            line = _WS_RE.sub(" ", tag.get_text(" ", strip=True)).strip()
            if len(line) >= _MIN_LINE_CHARS:
                lines.append(line)

        if not lines:
            raw = container.get_text("\n", strip=True)
            lines = [_WS_RE.sub(" ", line).strip() for line in raw.split("\n")]
            lines = [line for line in lines if len(line) >= _MIN_LINE_CHARS]

        unique: list[str] = []
        for line in lines:
            if not unique or line != unique[-1]:
                unique.append(line)
        return "\n".join(unique)
