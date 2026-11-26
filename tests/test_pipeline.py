"""NewsPipeline の要約・レビュー・修正ループの単体テスト."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.ai_client import (
    AIClientError,
    ReviewResult,
    SummaryResult,
)
from src.config import Config, load_config
from src.content_extractor import ArticleContent
from src.models import (
    APPROVED,
    INSUFFICIENT_CONTENT,
    REVIEW_REJECTED,
    SUMMARY_FAILED,
    FeedItem,
)
from src.pipeline import NewsPipeline
from src.rss_client import FeedFetchResult

BASE_CONFIG: Config = load_config(Path(__file__).resolve().parent.parent)


def make_config(tmp_path: Path, **overrides) -> Config:
    return replace(
        BASE_CONFIG,
        database_path=tmp_path / "news.db",
        max_articles_per_run=2,
        max_revision_attempts=1,
        **overrides,
    )


FIRST_SUMMARY = (
    "サンプル会社が発表しました\n"
    "個人情報の漏えいを防ぐためです\n"
    "警察に相談しました"
)

GOOD_SUMMARY = (
    "サンプル会社が新サービスを発表しました\n"
    "個人情報の漏えいを防ぐためです\n"
    "警察に相談しました"
)


def make_summary(status: str = "OK", text: str = GOOD_SUMMARY) -> SummaryResult:
    return SummaryResult(
        status=status,
        summary=text,
        key_facts=("事実",),
        reason="記事に明記された内容",
        location="東京都",
        numbers=("5万件",),
        uncertainties=(),
    )


def make_review(
    status: str = "PASS",
    reason: str = "問題ありません",
    **kwargs,
) -> ReviewResult:
    return ReviewResult(
        status=status,
        issues=tuple(kwargs.get("issues", ())),
        unsupported_claims=tuple(kwargs.get("unsupported_claims", ())),
        missing_facts=tuple(kwargs.get("missing_facts", ())),
        corrections=tuple(kwargs.get("corrections", ())),
        reason=reason,
        keyword_explanation_ok=kwargs.get("keyword_explanation_ok", True),
        unexplained_terms=tuple(kwargs.get("unexplained_terms", ())),
    )


class FakeAI:
    def __init__(self, summaries=(), reviews=()) -> None:
        self._summaries = list(summaries)
        self._reviews = list(reviews)
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def _next(self, items, label):
        if isinstance(items[0], Exception):
            raise items.pop(0)
        return items.pop(0)

    def summarize(self, prompt_text: str, **kwargs) -> SummaryResult:
        self.calls.append("summarize")
        self.prompts.append(prompt_text)
        return self._next(self._summaries, "summarize")

    def review(self, prompt_text: str) -> ReviewResult:
        self.calls.append("review")
        self.prompts.append(prompt_text)
        return self._next(self._reviews, "review")

    def revise(self, prompt_text: str, **kwargs) -> SummaryResult:
        self.calls.append("revise")
        self.prompts.append(prompt_text)
        return self._next(self._summaries, "revise")


class FakeExtractor:
    def __init__(
        self,
        text: str = "記事本文です。" * 30,
        ok: bool = True,
        error: str = "記事本文を取得できませんでした",
    ) -> None:
        self.text = text
        self.ok = ok
        self.error = error

    def fetch(self, url: str) -> ArticleContent:
        return ArticleContent(
            url=url,
            text=self.text if self.ok else "",
            char_count=len(self.text) if self.ok else 0,
            is_sufficient=self.ok,
            meets_guideline=self.ok,
            error="" if self.ok else self.error,
        )


class FakeRSS:
    def __init__(self, results=()) -> None:
        self.results = tuple(results)

    def fetch_all(self, feeds):
        return self.results


def one_item() -> FeedItem:
    return FeedItem(
        title="サンプル会社が新サービスを発表　個人情報の漏えいを防止",
        link="https://example.com/news/1",
        guid="guid-pipeline-1",
        source="テスト配信",
    )


def build_pipeline(tmp_path: Path, ai: FakeAI, **overrides) -> NewsPipeline:
    config = make_config(tmp_path, **overrides)
    pipeline = NewsPipeline(
        config,
        rss_client=FakeRSS(),
        extractor=FakeExtractor(),
        ai_client=ai,
    )
    pipeline.store.initialize()
    pipeline.store.discover((one_item(),))
    return pipeline


def test_pass_approves_and_saves_summary(tmp_path: Path) -> None:
    ai = FakeAI(
        summaries=[make_summary(text=GOOD_SUMMARY)],
        reviews=[make_review()],
    )
    pipeline = build_pipeline(tmp_path, ai)
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.approved is True
    assert result.summary == GOOD_SUMMARY
    assert ai.calls == ["summarize", "review"]
    saved = pipeline.store.get("guid:guid-pipeline-1")
    assert saved is not None
    assert saved.status == APPROVED
    assert saved.review_status == "PASS"
    assert saved.summary == GOOD_SUMMARY


def test_fail_then_revise_then_pass(tmp_path: Path) -> None:
    ai = FakeAI(
        summaries=[
            make_summary(text=FIRST_SUMMARY),
            make_summary(text=GOOD_SUMMARY),
        ],
        reviews=[
            make_review(
                "FAIL",
                "根拠のない表現があります",
                issues=["推測"],
                keyword_explanation_ok=False,
                unexplained_terms=["dots"],
            ),
            make_review("PASS", "修正されました"),
        ],
    )
    pipeline = build_pipeline(tmp_path, ai)
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.approved is True
    assert result.summary == GOOD_SUMMARY
    assert result.review_attempts == 2
    assert ai.calls == ["summarize", "review", "revise", "review"]
    assert FIRST_SUMMARY in ai.prompts[2]
    assert "推測" in ai.prompts[2]
    assert "説明が不足している用語: dots" in ai.prompts[2]


def test_all_reviews_fail_is_rejected(tmp_path: Path) -> None:
    ai = FakeAI(
        summaries=[make_summary(text=GOOD_SUMMARY), make_summary(text=GOOD_SUMMARY)],
        reviews=[make_review("FAIL", "事実が違う"), make_review("FAIL", " still 不自然")],
    )
    pipeline = build_pipeline(tmp_path, ai)
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.status == REVIEW_REJECTED
    assert result.approved is False
    saved = pipeline.store.get("guid:guid-pipeline-1")
    assert saved is not None
    assert saved.status == REVIEW_REJECTED
    assert saved.review_status == "FAIL"


def test_insufficient_body_stops_before_ai(tmp_path: Path) -> None:
    ai = FakeAI()
    config = make_config(tmp_path)
    pipeline = NewsPipeline(
        config,
        rss_client=FakeRSS(),
        extractor=FakeExtractor(ok=False, error=""),
        ai_client=ai,
    )
    pipeline.store.initialize()
    pipeline.store.discover((one_item(),))
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.status == INSUFFICIENT_CONTENT
    assert ai.calls == []


def test_summary_error_is_fail_closed(tmp_path: Path) -> None:
    ai = FakeAI(summaries=[AIClientError("shortcuts 実行失敗")])
    pipeline = build_pipeline(tmp_path, ai)
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.status == SUMMARY_FAILED
    assert ai.calls == ["summarize"]
    saved = pipeline.store.get("guid:guid-pipeline-1")
    assert saved is not None
    assert "要約生成に失敗" in saved.error_message


def test_ai_insufficient_status_stops(tmp_path: Path) -> None:
    ai = FakeAI(summaries=[make_summary("INSUFFICIENT", "")])
    pipeline = build_pipeline(tmp_path, ai)
    article = pipeline.store.get("guid:guid-pipeline-1")
    assert article is not None

    result = pipeline.process_one(article)
    assert result.status == INSUFFICIENT_CONTENT
    assert ai.calls == ["summarize"]


def test_run_once_discovers_and_processes(tmp_path: Path) -> None:
    ai = FakeAI(
        summaries=[make_summary(text=GOOD_SUMMARY)],
        reviews=[make_review()],
    )
    config = make_config(tmp_path)
    pipeline = NewsPipeline(
        config,
        rss_client=FakeRSS(
            [
                FeedFetchResult(
                    feed_name="テスト配信",
                    url="https://example.com/rss.xml",
                    items=(one_item(),),
                )
            ]
        ),
        extractor=FakeExtractor(),
        ai_client=ai,
    )
    result = pipeline.run_once()
    assert result.discovered == 1
    assert result.approved_count == 1
    assert result.feed_errors == ()
