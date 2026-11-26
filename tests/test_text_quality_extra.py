"""本文ノイズ除去と必須要素ガードのテスト."""

from __future__ import annotations

from src.text_quality import missing_required_elements, trim_related_articles


def test_trim_related_articles_removes_link_block():
    body = "本文です。\nITmedia NEWS\n【注目】関連記事A\n【関連】関連記事B"
    assert trim_related_articles(body) == "本文です。"


def test_trim_related_articles_keeps_plain_body():
    body = "本文だけです。"
    assert trim_related_articles(body) == body


def test_missing_required_elements_accepts_complete_summary():
    title = "米マイクロソフトがコードレッドを宣言　大規模障害で対応見直し"
    summary = (
        "米マイクロソフトがコードレッドを宣言しました\n"
        "大規模障害で対応を見直します\n"
        "復旧を1時間以内に目指します"
    )
    assert missing_required_elements(summary, title) == ()


def test_missing_required_elements_rejects_short_summary():
    title = "APIルーターに注意　428件を調べてわかった乗っ取り手口"
    summary = "悪意あるルーターの攻撃を確認しました。"
    missing = missing_required_elements(summary, title)
    assert "最低文字数" in missing
    assert "主体・出来事名" in missing


def test_missing_required_elements_rejects_unnatural_predicate():
    title = "米OpenAIがdotsを解禁　月額3000円"
    summary = "米OpenAIがdotsという常時稼働AIエージェントの提供を開始するとしました。月額は3000円です。"
    missing = missing_required_elements(summary, title)
    assert "不自然な述語" in missing
