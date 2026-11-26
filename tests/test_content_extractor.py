"""ContentExtractor の単体テスト."""

from __future__ import annotations

import httpx

from src.content_extractor import ContentExtractor

BODY = (
    "東京都足立区に本社を置くITサービス企業は25日、外部からの不正アクセスを受け、"
    "顧客の氏名やメールアドレスなど最大約5万件の個人情報が外部に流出した可能性があると発表した。"
    "同社によると、システムの脆弱性を突かれて社内の管理サーバーに侵入されたという。"
    "原因や侵入の詳しい経緯については現在調査を進めており、対象の顧客には順次連絡する。"
    "なお、警察にも相談している。"
)

HTML = f"""<html><head><title>記事</title><style>.x{{color:red}}</style></head>
<body>
<nav>ナビゲーションの長い文字列は本文に含めないでください。」</nav>
<script>var secret = '本文に含めないスクリプト文字列です';</script>
<article>
  <h1>見出し</h1>
  <p>{BODY}</p>
  <p>{BODY}</p>
</article>
<footer>フッターの長い文字列も本文に含めないでください。」</footer>
</body></html>
"""


def test_extract_prefers_article_and_removes_noise() -> None:
    extractor = ContentExtractor(min_chars_guideline=400)
    result = extractor.extract(HTML, "https://example.com/news/1")
    assert result.error == ""
    assert result.ok is True
    assert result.text == BODY  # 同一段落の重複は除去される
    assert "ナビゲーション" not in result.text
    assert "スクリプト文字列" not in result.text
    assert "フッター" not in result.text
    assert result.char_count > 100
    assert result.meets_guideline is False  # 180文字なので400文字の目安には達しない


def test_short_body_is_not_sufficient() -> None:
    extractor = ContentExtractor(min_chars_guideline=400)
    result = extractor.extract("<article><p>短いです。</p></article>", "https://example.com/short")
    assert result.ok is False
    assert result.is_sufficient is False
    assert result.meets_guideline is False


def test_fetch_returns_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    extractor = ContentExtractor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = extractor.fetch("https://example.com/missing")
    assert result.ok is False
    assert "404" in result.error


def test_fetch_extracts_body_from_http_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("NewsTelegramPersonalBot")
        return httpx.Response(200, text=HTML)

    extractor = ContentExtractor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = extractor.fetch("https://example.com/news/1")
    assert result.ok is True
    assert "不正アクセス" in result.text


def test_fetch_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(
                302, headers={"Location": "https://example.com/new"}
            )
        return httpx.Response(200, text=HTML)

    extractor = ContentExtractor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = extractor.fetch("https://example.com/old")
    assert result.error == ""
    assert result.ok is True
    assert "不正アクセス" in result.text
