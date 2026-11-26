"""本文ノイズ除去と必須要素ガードのテスト."""

from __future__ import annotations

from src.text_quality import (
    has_multiple_events,
    has_role_without_name,
    has_vague_evaluation,
    missing_required_elements,
    missing_who_what,
    trim_related_articles,
    unexplained_terms,
)


def test_trim_related_articles_removes_link_block():
    body = "本文です。\nITmedia NEWS\n【注目】関連記事A\n【関連】関連記事B"
    assert trim_related_articles(body) == "本文です。"


def test_trim_related_articles_removes_itmedia_footer():
    """実記事の著作権行以降（関連記事・ランキング）も除去する。"""
    body = (
        "NTTドコモで3度の通信障害が発生しました。\n"
        "Copyright © ITmedia, Inc. All Rights Reserved.\n"
        "ドコモ3度の通信障害「大変遺憾」と総務相\n"
        "アクセスランキング\n1 無関係な記事"
    )
    assert trim_related_articles(body) == (
        "NTTドコモで3度の通信障害が発生しました。"
    )


def test_trim_related_articles_keeps_plain_body():
    body = "本文だけです。"
    assert trim_related_articles(body) == body


def test_missing_required_elements_accepts_complete_summary():
    title = "米マイクロソフトがコードレッドを宣言　大規模障害で対応見直し"
    summary = (
        "米マイクロソフトがコードレッドという非常事態を宣言しました\n"
        "大規模障害で対応を見直し、復旧を1時間以内に目指します"
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


def test_missing_required_elements_rejects_three_lines():
    title = "月に3度の障害　揺らぐドコモの信頼"
    summary = "NTTドコモが障害を起こしました。\n利用者が不満を述べました。\n大臣が対応を求めました。"
    missing = missing_required_elements(summary, title)
    assert "2行以内" in missing


def test_ntt_vague_summary_is_rejected():
    """実際に通知された低品質要約を機械ガードで拒否する。"""
    title = "月に3度の障害……揺らぐドコモの信頼　通信品質が安定しない理由は？"
    summary = "NTTドコモへの信頼が揺らぎつつあります。藤井総務省が原因究明をもとめました。"
    missing = missing_required_elements(summary, title)
    assert "具体性のない評価" in missing
    assert "WHO/WHAT（何についてか）" in missing
    assert has_vague_evaluation(summary) is True
    assert missing_who_what(summary) is True


def test_concrete_event_allows_evaluative_wording():
    """背景イベントが要約内にある評価は機械ガードを通す（AIレビューが最終判断）。"""
    summary = "NTTドコモが11月に3度の通信障害を起こしました。藤井総務相が再発防止を求めました。"
    assert has_vague_evaluation(summary) is False
    assert missing_who_what(summary) is False


def test_ai_generated_summary_without_explanation_is_rejected():
    """実機で PASS した要約を、初見用語と役職-only の観点で拒否する。"""
    title = "月に3度の障害……揺らぐドコモの信頼　通信品質が安定しない理由は？"
    summary = "NTTドコモは11月5日、11日、16日に5G SAで通信障害が発生しました。総務相は原因究明を求めました。"
    missing = missing_required_elements(summary, title)
    assert "初見用語の説明: 5G SA" in missing
    assert "WHO（固有名詞の主体）" in missing
    assert unexplained_terms(summary) == ("5G SA",)
    assert has_role_without_name(summary) is True


def test_named_actor_with_explanation_is_accepted():
    """氏名と用語説明を入れ、主要イベント1つに絞った要約は機械ガードを通る。"""
    title = "月に3度の障害……揺らぐドコモの信頼　通信品質が安定しない理由は？"
    summary = "藤井総務相が通信障害の原因究明を求めたNTTドコモが、5G SAという5G専用設備で3度の障害を起こしました。"
    assert missing_required_elements(summary, title) == ()
    assert unexplained_terms(summary) == ()
    assert has_role_without_name(summary) is False
    assert has_multiple_events(summary) is False


def test_two_independent_event_sentences_are_rejected():
    """再現試験でPASSした2文構成を、単一イベント要件で拒否する。"""
    title = "月に3度の障害……揺らぐドコモの信頼　通信品質が安定しない理由は？"
    summary = "11月5日、11日、16日にNTTドコモで3度の通信障害が発生しました。藤井総務相が原因究明を求めました。"
    missing = missing_required_elements(summary, title)
    assert "単一イベント" in missing
    assert "WHO/WHAT（何についてか）" in missing
    assert has_multiple_events(summary) is True
