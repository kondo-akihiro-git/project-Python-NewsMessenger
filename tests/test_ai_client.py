"""ShortcutAIClient および JSON/プロンプト検証の単体テスト."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.ai_client import (
    AIClientError,
    AIRateLimitError,
    AITimeoutError,
    InvalidAIResponseError,
    ShortcutAIClient,
    SummaryQualityError,
    ShortcutNotFoundError,
    extract_json_object,
    render_prompt,
    validate_review_payload,
    validate_summary_payload,
)


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------

def test_render_prompt_preserves_json_braces(tmp_path: Path) -> None:
    tpl = tmp_path / "p.txt"
    tpl.write_text('{"status": "OK"}\n見出し: {{TITLE}}\n本文: {{ARTICLE_BODY}}', encoding="utf-8")
    rendered = render_prompt(tpl, {"TITLE": "テスト", "ARTICLE_BODY": "内容です。"})
    assert '{"status": "OK"}' in rendered
    assert "見出し: テスト" in rendered
    assert "本文: 内容です。" in rendered


# ---------------------------------------------------------------------------
# extract_json_object
# ---------------------------------------------------------------------------

def test_extract_json_object_from_code_fence_and_prose() -> None:
    raw = 'はい、結果はこちらです。\n```json\n{"status": "OK", "summary": "要約です。"}\n```\n以上です。'
    parsed = extract_json_object(raw)
    assert parsed["status"] == "OK"
    assert parsed["summary"] == "要約です。"


# ---------------------------------------------------------------------------
# validate_summary_payload
# ---------------------------------------------------------------------------

def test_validate_summary_payload_accepts_1_line_without_url() -> None:
    data = {
        "status": "OK",
        "summary": "足立区の会社が攻撃を受け約5万件の情報が漏れ警察に相談し被害を報告しました。",
        "key_facts": ["約5万件流出"],
        "reason": "システムの脆弱性を突かれた",
        "location": "東京都足立区",
        "numbers": ["約5万件"],
        "uncertainties": ["詳しい侵入経路"],
    }
    res = validate_summary_payload(data)
    assert res.status == "OK"
    assert "\n" not in res.summary


def test_extract_json_object_repairs_japanese_closing_quote() -> None:
    raw = '{"status":"PASS","issues":[],"reason":"問題ありません。」}'
    parsed = extract_json_object(raw)
    assert parsed["status"] == "PASS"
    assert parsed["reason"] == "問題ありません。"


def test_validate_summary_payload_rejects_taigen_dome() -> None:
    base = {
        "status": "OK",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(InvalidAIResponseError, match="体言止め"):
        validate_summary_payload(
            {
                **base,
                "summary": "米マイクロソフトがコードレッドを宣言しました\n大規模障害で対応を見直し対策",
            }
        )


def test_validate_summary_payload_rejects_vague_evaluation_and_missing_what() -> None:
    """NTTドコモ/藤井総務省の低品質要約と同種の表現を拒否する。"""
    base = {
        "status": "OK",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(InvalidAIResponseError, match="根拠のない評価"):
        validate_summary_payload(
            {
                **base,
                "summary": "NTTドコモへの信頼が揺らぎつつあります。\n藤井総務相が原因究明を求めました。",
            }
        )
    with pytest.raises(InvalidAIResponseError, match="WHO/WHAT"):
        validate_summary_payload(
            {
                **base,
                "summary": "NTTドコモが原因究明を求めました。\n詳しい理由は不明です。",
            }
        )


def test_validate_summary_payload_rejects_unexplained_term_and_role_only() -> None:
    base = {
        "status": "OK",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(InvalidAIResponseError, match="初見用語"):
        validate_summary_payload(
            {
                **base,
                "summary": "NTTドコモは11月に5G SAで通信障害が発生しました。藤井総務相が調べます。",
            }
        )
    with pytest.raises(InvalidAIResponseError, match="役職"):
        validate_summary_payload(
            {
                **base,
                "summary": "NTTドコモは11月に通信障害が発生しました。総務相が再発防止を求めました。",
            }
        )


def test_validate_summary_payload_rejects_multiple_events() -> None:
    base = {
        "status": "OK",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(InvalidAIResponseError, match="複数イベント"):
        validate_summary_payload(
            {
                **base,
                "summary": "11月にNTTドコモで3度の通信障害が発生しました。藤井総務相が再発防止を求めました。",
            }
        )


def test_validate_summary_payload_rejects_url_and_over_2_lines() -> None:
    base = {
        "status": "OK",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(InvalidAIResponseError):
        validate_summary_payload({**base, "summary": "要約です https://example.com"})
    with pytest.raises(InvalidAIResponseError):
        validate_summary_payload({**base, "summary": "1行目\n2行目\n3行目"})
    with pytest.raises(InvalidAIResponseError):
        validate_summary_payload({**base, "summary": "要約：本文です。"})


# ---------------------------------------------------------------------------
# validate_review_payload
# ---------------------------------------------------------------------------

def test_validate_review_payload_pass_and_fail() -> None:
    ok = validate_review_payload(
        {
            "status": "PASS",
            "issues": [],
            "unsupported_claims": [],
            "missing_facts": [],
            "corrections": [],
            "keyword_explanation_ok": True,
            "unexplained_terms": [],
            "concrete_basis_ok": True,
            "four_w_one_h_ok": True,
            "single_event_ok": True,
            "missing_4w1h": [],
            "reason": "正確です",
        }
    )
    assert ok.status == "PASS"

    fail = validate_review_payload(
        {
            "status": "FAIL",
            "issues": ["数値誤り"],
            "unsupported_claims": ["新宿区"],
            "missing_facts": [],
            "corrections": ["足立区に直す"],
            "keyword_explanation_ok": True,
            "unexplained_terms": [],
            "concrete_basis_ok": True,
            "four_w_one_h_ok": True,
            "single_event_ok": True,
            "missing_4w1h": [],
            "reason": "事実と異なる",
        }
    )
    assert fail.status == "FAIL"
    assert fail.unsupported_claims == ("新宿区",)


def test_keyword_explanation_failure_forces_review_fail() -> None:
    """AIがPASSと返しても説明不足が応答されれば不合格にする。"""
    result = validate_review_payload(
        {
            "status": "PASS",
            "issues": [],
            "unsupported_claims": [],
            "missing_facts": [],
            "corrections": [],
            "keyword_explanation_ok": False,
            "unexplained_terms": ["dots"],
            "concrete_basis_ok": True,
            "four_w_one_h_ok": True,
            "single_event_ok": True,
            "missing_4w1h": [],
            "reason": "問題ありません",
        }
    )
    assert result.status == "FAIL"
    assert result.keyword_explanation_ok is False
    assert result.unexplained_terms == ("dots",)
    assert any("説明" in issue for issue in result.issues)


def _review_data(**overrides):
    data = {
        "status": "PASS",
        "issues": [],
        "unsupported_claims": [],
        "missing_facts": [],
        "corrections": [],
        "keyword_explanation_ok": True,
        "unexplained_terms": [],
        "concrete_basis_ok": True,
        "four_w_one_h_ok": True,
        "single_event_ok": True,
        "missing_4w1h": [],
        "reason": "問題ありません",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"concrete_basis_ok": False}, "裏付け"),
        ({"four_w_one_h_ok": False}, "4W1H"),
        ({"missing_4w1h": ["WHO", "WHAT"]}, "WHO"),
        ({"single_event_ok": False}, "1つに絞られていません"),
    ],
)
def test_review_quality_flags_force_fail(overrides, expected) -> None:
    """PASSでも品質フラグが不合格ならFAILへ上書きする。"""
    result = validate_review_payload(_review_data(**overrides))
    assert result.status == "FAIL"
    assert any(expected in issue for issue in result.issues)
    assert expected in result.reason


def test_review_requires_new_quality_fields() -> None:
    data = _review_data()
    del data["concrete_basis_ok"]
    with pytest.raises(InvalidAIResponseError, match="concrete_basis_ok"):
        validate_review_payload(data)


def test_review_requires_keyword_explanation_field() -> None:
    with pytest.raises(InvalidAIResponseError, match="keyword_explanation_ok"):
        validate_review_payload(
            {
                "status": "PASS",
                "issues": [],
                "unsupported_claims": [],
                "missing_facts": [],
                "corrections": [],
                "unexplained_terms": [],
                "reason": "問題ありません",
            }
        )


# ---------------------------------------------------------------------------
# Popen モック用ヘルパー
# ---------------------------------------------------------------------------

_GOOD_SUMMARY_PAYLOAD = {
    "status": "OK",
    "summary": "足立区の会社から情報約5万件が漏れ警察に相談し被害状況を報告しました。",
    "key_facts": ["約5万件流出"],
    "reason": "不正アクセス",
    "location": "東京都足立区",
    "numbers": ["約5万件"],
    "uncertainties": [],
}


def _make_popen(cmd, *, returncode=0, stdout="", stderr="", output_payload=None, write_delay=0.0):
    """fake Popen を返すファクトリ関数を返す。
    output_payload が指定されると --output-path にその内容を書き込む。
    write_delay > 0 なら別スレッドで遅延書き込みする（非同期出力のシミュレーション）。
    """
    out_path = None
    if "--output-path" in cmd:
        out_idx = cmd.index("--output-path") + 1
        out_path = Path(cmd[out_idx])

    def _write():
        if write_delay > 0:
            time.sleep(write_delay)
        if out_path is not None and output_payload is not None:
            out_path.write_text(json.dumps(output_payload, ensure_ascii=False), encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.returncode = returncode

    def communicate(timeout=None):
        t = threading.Thread(target=_write, daemon=True)
        t.start()
        t.join(timeout=max((write_delay or 0) + 1.0, 2.0))
        return stdout, stderr

    mock_proc.communicate = communicate
    mock_proc.kill = MagicMock()
    return mock_proc


def _popen_factory_ok(payload=None, write_delay=0.0):
    """成功レスポンスを返す popen_factory を生成する。"""
    p = payload or _GOOD_SUMMARY_PAYLOAD

    def factory(cmd, **kwargs):
        return _make_popen(cmd, output_payload=p, write_delay=write_delay)

    return factory


def _popen_factory_error(returncode=1, stderr="Error: shortcuts failed"):
    def factory(cmd, **kwargs):
        return _make_popen(cmd, returncode=returncode, stderr=stderr)

    return factory


# ---------------------------------------------------------------------------
# ShortcutAIClient — run_shortcut 正常系
# ---------------------------------------------------------------------------

def test_run_shortcut_reads_output_file(tmp_path: Path) -> None:
    """出力ファイルに書かれた内容が返ること。"""
    client = ShortcutAIClient(
        popen_factory=_popen_factory_ok(),
        retry_delay_seconds=0.0,
        poll_interval=0.05,
        stable_for=0.1,
        max_wait=2.0,
    )
    result = client.run_shortcut("News_Summarize", "テスト入力")
    assert "status" in result  # JSON テキストが返る


def test_run_shortcut_waits_for_async_write(tmp_path: Path) -> None:
    """非同期書き込み（遅延0.3秒）でも正常に読み取れること。"""
    client = ShortcutAIClient(
        popen_factory=_popen_factory_ok(write_delay=0.3),
        retry_delay_seconds=0.0,
        poll_interval=0.05,
        stable_for=0.15,
        max_wait=3.0,
    )
    result = client.run_shortcut("News_Summarize", "テスト入力")
    parsed = json.loads(result)
    assert parsed["status"] == "OK"


# ---------------------------------------------------------------------------
# ShortcutAIClient — エラーマッピング
# ---------------------------------------------------------------------------

def test_shortcut_not_found_error() -> None:
    factory = _popen_factory_error(returncode=1, stderr="Error: Couldn't find shortcut 'News_Summarize'")
    client = ShortcutAIClient(popen_factory=factory, retry_delay_seconds=0.0, max_json_retries=0)
    with pytest.raises(ShortcutNotFoundError):
        client.summarize("dummy")


def test_rate_limit_error() -> None:
    factory = _popen_factory_error(returncode=1, stderr="Error: Apple Intelligence rate limit reached")
    client = ShortcutAIClient(popen_factory=factory, retry_delay_seconds=0.0, max_json_retries=0)
    with pytest.raises(AIRateLimitError):
        client.summarize("dummy")


def test_busy_apple_intelligence_error_is_rate_limit() -> None:
    """実機の「現在、リクエストを完了できません」も利用制限として扱う。"""
    factory = _popen_factory_error(returncode=1, stderr="Error: 現在、リクエストを完了できません。")
    client = ShortcutAIClient(popen_factory=factory, retry_delay_seconds=0.0, max_json_retries=4)
    with pytest.raises(AIRateLimitError):
        client.summarize("dummy")


def test_timeout_error() -> None:
    def timeout_factory(cmd, **kwargs):
        mock_proc = MagicMock()
        call_count = {"n": 0}

        def communicate_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 1回目: タイムアウト（本番の communicate(timeout=...) 呼び出し）
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)
            # 2回目: kill 後のクリーンアップ communicate() は空で返す
            return "", ""

        mock_proc.communicate = communicate_side_effect
        mock_proc.kill = MagicMock()
        return mock_proc

    client = ShortcutAIClient(
        popen_factory=timeout_factory,
        timeout_seconds=1.0,
        retry_delay_seconds=0.0,
        max_json_retries=0,
    )
    with pytest.raises(AITimeoutError):
        client.summarize("dummy")


def test_oserror_raises_ai_client_error() -> None:
    def oserror_factory(cmd, **kwargs):
        raise OSError("No such file")

    client = ShortcutAIClient(
        popen_factory=oserror_factory,
        retry_delay_seconds=0.0,
        max_json_retries=0,
    )
    with pytest.raises(AIClientError):
        client.summarize("dummy")


# ---------------------------------------------------------------------------
# ShortcutAIClient — リトライ
# ---------------------------------------------------------------------------

def test_retries_broken_json_then_succeeds() -> None:
    """1回目は壊れたJSON → 2回目に正常なJSONで成功すること。"""
    call_count = {"n": 0}

    def factory(cmd, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 壊れた出力
            payload_text = "壊れた出力"
        else:
            payload_text = json.dumps(_GOOD_SUMMARY_PAYLOAD, ensure_ascii=False)

        out_idx = cmd.index("--output-path") + 1
        out_path = Path(cmd[out_idx])

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        def communicate(timeout=None):
            out_path.write_text(payload_text, encoding="utf-8")
            return "", ""

        mock_proc.communicate = communicate
        mock_proc.kill = MagicMock()
        return mock_proc

    client = ShortcutAIClient(
        popen_factory=factory,
        retry_delay_seconds=0.0,
        max_json_retries=2,
        poll_interval=0.05,
        stable_for=0.1,
        max_wait=2.0,
    )
    res = client.summarize("dummy prompt")
    assert call_count["n"] == 2
    assert res.status == "OK"


# ---------------------------------------------------------------------------
# _read_stable_output
# ---------------------------------------------------------------------------

def test_read_stable_output_returns_empty_on_missing_file(tmp_path: Path) -> None:
    client = ShortcutAIClient(poll_interval=0.05, stable_for=0.1, max_wait=0.3)
    result = client._read_stable_output(tmp_path / "nonexistent.txt", max_wait=0.3)
    assert result == ""


def test_read_stable_output_reads_stable_file(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    out.write_text("hello world", encoding="utf-8")
    client = ShortcutAIClient(poll_interval=0.05, stable_for=0.1, max_wait=2.0)
    result = client._read_stable_output(out, poll_interval=0.05, stable_for=0.1, max_wait=2.0)
    assert result == "hello world"


def test_extract_json_object_repairs_raw_newlines_in_string() -> None:
    """AIがJSON文字列内に生の改行を出力しても解析できること。"""
    raw = '{"status":"OK","summary":"1行目\n2行目\n3行目","key_facts":[]}'
    parsed = extract_json_object(raw)
    assert parsed["summary"] == "1行目\n2行目\n3行目"


def test_extract_json_object_recovers_truncated_summary_payload() -> None:
    """status/summary直後で出力が切れても、要約本文を残して不明を明示すること。"""
    raw = '{"status":"OK","summary":"会社が障害を宣言しました。\\n復旧を終えるとしています。",'
    parsed = extract_json_object(raw)
    assert parsed["status"] == "OK"
    assert parsed["summary"] == "会社が障害を宣言しました。\n復旧を終えるとしています。"
    assert parsed["key_facts"] == []
    assert parsed["reason"] == "不明"
    assert "AI出力が途中で終了" in parsed["uncertainties"][0]


def test_extract_json_object_does_not_recover_empty_summary() -> None:
    """要約が空で切り上げられた場合は復旧候補を作らないこと。"""
    raw = '{"status":"OK","summary":"",'
    with pytest.raises(InvalidAIResponseError):
        extract_json_object(raw)


def test_ensure_str_coerces_string_list() -> None:
    """AIが文字列ではなく文字列配列を返しても、連結して受け入れること。"""
    payload = {
        "status": "OK",
        "summary": "サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。",
        "key_facts": [],
        "reason": ["記事に明記なし"],
        "location": ["東京都", "新宿区"],
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(payload, max_chars=55)
    assert result.reason == "記事に明記なし"
    assert result.location == "東京都、新宿区"


def test_length_failure_adds_shortening_instruction_on_retry() -> None:
    """文字数超過の再試行では、短くする指示のみを付け、事実追加は指示しないこと。"""
    base = {
        "status": "OK",
        "summary": "あ" * 60,
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    fixed = dict(base, summary="サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。")
    payloads = [
        json.dumps(base, ensure_ascii=False),
        json.dumps(fixed, ensure_ascii=False),
    ]
    prompts: list[str] = []

    def fake_run(shortcut, prompt):
        prompts.append(prompt)
        return payloads[len(prompts) - 1]

    client = ShortcutAIClient(
        summarize_shortcut="News_Summarize", max_json_retries=1, retry_delay_seconds=0.0
    )
    client.run_shortcut = fake_run
    result = client.summarize("base prompt", max_chars=55)
    assert len(prompts) == 2
    assert "再生成指示" in prompts[1]
    assert "60文字で上限（55文字）を超えました" in prompts[1]
    assert "あと5文字以上を" in prompts[1]
    assert "あ" * 60 in prompts[1]
    assert "上限以内に短くしてください" in prompts[1]
    assert "事実を削ってはいけません" in prompts[1]
    assert result.summary == "サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。"


def test_over_limit_summary_is_compacted_only_with_safe_predicate() -> None:
    """超過は「発生しました」→「発生した」だけに留め、不自然な述語を作らない。"""
    payload = {
        "status": "OK",
        "summary": "あ" * 49 + "発生しました。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(payload, max_chars=55)
    assert len(result.summary) == 54
    assert result.summary.endswith("発生した。")


def test_about_phrase_is_compacted_without_changing_facts() -> None:
    """「〜について議論」→「〜を議論」で57文字を自然な54文字へ収める。"""
    summary = (
        "藤井総務相は、飲食店のメニューにAIで生成した画像を使用した場合、"
        "景品表示法違反ではないかについて議論しています。"
    )
    assert len(summary) == 57
    payload = {
        "status": "OK",
        "summary": summary,
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(payload, max_chars=55)
    assert result.summary.endswith("違反ではないかを議論しています。")
    assert len(result.summary) == 54


def test_overlong_supplement_line_is_dropped_when_first_line_is_valid() -> None:
    """超過時に別記事由来の補足行を落とし、主要ニュース1行だけを残す。"""
    first_line = (
        "飲食店が生成AIで生成した画像を使用した場合、景品表示法違反になるか検討しています。"
    )
    payload = {
        "status": "OK",
        "summary": first_line + "\n藤井総務相が原因究明を求めました。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(
        payload,
        title="飲食店の“AI商品画像”は景品表示法違反にならないの？　弁護士の見解(ITmedia NEWS)",
        article_body="飲食店のメニューに生成AIで作った画像を使うことについて弁護士の見解を紹介します。",
        max_chars=55,
    )
    assert result.summary == first_line
    assert len(result.summary) <= 55


def test_overlong_sentence_without_newline_is_dropped() -> None:
    """AIが改行せず「。」だけで繋いだ補足文も2行目として扱う。"""
    first_line = (
        "飲食店が生成AIで生成した画像を使用した場合、景品表示法違反となる可能性があります。"
    )
    payload = {
        "status": "OK",
        "summary": first_line + "生成AIを利用した画像は、商品の撮影や画像制作にかかる手間を減らす。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(
        payload,
        title="飲食店の“AI商品画像”は景品表示法違反にならないの？　弁護士の見解(ITmedia NEWS)",
        max_chars=55,
    )
    assert result.summary == first_line


def test_cause_phrase_is_compacted_to_fit_one_character_over() -> None:
    """56文字の1行要約を「により」→「で」で55文字以内に収める。"""
    summary = "文部科学省は11月25日に不正アクセスにより最大16万3953件の情報が外部に漏えいした可能性があると発表した。"
    assert len(summary) == 56
    payload = {
        "status": "OK",
        "summary": summary,
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(
        payload,
        title="文部科学省の入札システムに不正アクセス　最大16万件漏えいか",
        max_chars=55,
    )
    assert "不正アクセスで" in result.summary
    assert len(result.summary) <= 55


def test_supplement_line_dropped_even_if_second_title_keyword_is_only_there() -> None:
    """補足行にしか無い見出し語があっても1行目が健全なら補足を落とせる。"""
    first_line = "米Xは11月25日に13～17歳のユーザー向けにペアレンタルコントロール機能を提供した。"
    payload = {
        "status": "OK",
        "summary": first_line + "\n保護者は利用時間やコンテンツを管理できます。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    result = validate_summary_payload(
        payload,
        title="X、「ペアレンタルコントロール」を導入　メッセージ内容は見られず(ITmedia NEWS)",
        max_chars=55,
    )
    assert result.summary == first_line


def test_prompts_do_not_embed_past_article_examples() -> None:
    """過去記事の実名例がプロンプトへ戻ると別記事へコピーされるため禁止する。"""
    prompt_dir = Path(__file__).resolve().parents[1] / "config" / "prompts"
    contamination = ("藤井総務相", "NTTドコモ", "OpenAI", "dots", "KDDI", "5G SA")
    for path in sorted(prompt_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        for token in contamination:
            assert token not in text, f"{path.name} に過去記事の例 {token} があります"
        assert "例文のコピー禁止" in text


def test_corporate_suffix_requires_article_basis() -> None:
    """法人格表記は本文・見出しに根拠がある場合だけ許可する。"""
    payload = {
        "status": "OK",
        "summary": "株式会社サンプルが通信障害の原因究明と再発防止策を総務省に求めました。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(SummaryQualityError, match="法人格"):
        validate_summary_payload(payload)
    result = validate_summary_payload(
        payload, article_body="株式会社サンプルは11月27日に発表しました。"
    )
    assert "株式会社サンプル" in result.summary


def test_unnatural_predicate_is_rejected_without_title() -> None:
    """見出しが渡されなくても不自然な述語は品質不合格とする。"""
    payload = {
        "status": "OK",
        "summary": "NTTドコモが通信障害の原因究明としました。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    with pytest.raises(SummaryQualityError, match="不自然な述語"):
        validate_summary_payload(payload)


def test_taigen_failure_adds_predicate_instruction_on_retry() -> None:
    """体言止めの再試行では、直前の要約を渡して述語で終えるよう指示すること。"""
    base = {
        "status": "OK",
        "summary": "会社が障害を発表し、対応を検討",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    fixed = dict(base, summary="サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。")
    payloads = [json.dumps(base, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)]
    prompts: list[str] = []

    def fake_run(shortcut, prompt):
        prompts.append(prompt)
        return payloads[len(prompts) - 1]

    client = ShortcutAIClient(
        summarize_shortcut="News_Summarize", max_json_retries=1, retry_delay_seconds=0.0
    )
    client.run_shortcut = fake_run
    result = client.summarize("base prompt", max_chars=55)
    assert len(prompts) == 2
    assert "体言止め" in prompts[1]
    assert "会社が障害を発表し、対応を検討" in prompts[1]
    assert "述語（〜しました／〜します／〜です）で終えてください" in prompts[1]
    assert result.summary == "サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。"


def test_external_ai_error_is_not_retried() -> None:
    """AI障害は品質不合格と異なり、リトライせず即座に失敗させる。"""
    calls: list[str] = []

    def fake_run(shortcut, prompt):
        calls.append(shortcut)
        raise AIClientError("オンデバイスAI実行失敗")

    client = ShortcutAIClient(
        summarize_shortcut="News_Summarize", max_json_retries=3, retry_delay_seconds=0.0
    )
    client.run_shortcut = fake_run
    with pytest.raises(AIClientError):
        client.summarize("base prompt")
    assert calls == ["News_Summarize"]


def test_compact_retry_template_is_used_with_previous_summary() -> None:
    """専用の短縮プロンプトが渡され、元の長いプロンプトへ追記しないこと。"""
    long_summary = "あ" * 60
    base = {
        "status": "OK",
        "summary": long_summary,
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    fixed = dict(base, summary="サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。")
    payloads = [json.dumps(base, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)]
    prompts: list[str] = []

    def fake_run(shortcut, prompt):
        prompts.append(prompt)
        return payloads[len(prompts) - 1]

    template = "短縮: {{PREVIOUS_SUMMARY}} / {{OVER_CHARS}}文字 / {{MAX_CHARS}}文字以内"
    client = ShortcutAIClient(
        summarize_shortcut="News_Summarize", max_json_retries=1, retry_delay_seconds=0.0
    )
    client.run_shortcut = fake_run
    result = client.summarize("base prompt", max_chars=55, retry_template=template)
    assert len(prompts) == 2
    assert prompts[1] == f"短縮: {long_summary} / 60文字 / 55文字以内"
    assert "base prompt" not in prompts[1]
    assert result.summary == "サンプル会社が新サービスを発表し個人情報保護のため警察に相談しました。"


def test_quality_failure_uses_compact_retry_template() -> None:
    """品質ガード抵触時も長い元プロンプトへ戻さず、短縮プロンプトで修正する。"""
    base = {
        "status": "OK",
        "summary": "NTTドコモは11月に5G SAで3度の障害を起こしました。",
        "key_facts": [],
        "reason": "不明",
        "location": "不明",
        "numbers": [],
        "uncertainties": [],
    }
    fixed = dict(
        base, summary="NTTドコモは5G SAという5G専用設備で3度の障害を起こしました。"
    )
    payloads = [json.dumps(base, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)]
    prompts: list[str] = []

    def fake_run(shortcut, prompt):
        prompts.append(prompt)
        return payloads[len(prompts) - 1]

    template = "修正: {{PREVIOUS_SUMMARY}} / {{FAILURE_INSTRUCTION}}"
    client = ShortcutAIClient(
        summarize_shortcut="News_Summarize", max_json_retries=1, retry_delay_seconds=0.0
    )
    client.run_shortcut = fake_run
    result = client.summarize("base prompt", max_chars=55, retry_template=template)
    assert len(prompts) == 2
    assert "base prompt" not in prompts[1]
    assert "初見用語" in prompts[1]
    assert result.summary == fixed["summary"]


def test_read_stable_output_waits_for_complete_json(tmp_path: Path) -> None:
    """途中まで書き込まれたJSONを採用せず、完全な出力まで待つこと。"""
    out = tmp_path / "out.txt"
    out.write_text('{"status":"OK","summary":"途中', encoding="utf-8")

    def complete_later() -> None:
        time.sleep(0.3)
        out.write_text(
            json.dumps({"status": "OK", "summary": "完了"}, ensure_ascii=False),
            encoding="utf-8",
        )

    threading.Thread(target=complete_later, daemon=True).start()
    client = ShortcutAIClient(poll_interval=0.05, stable_for=0.1, max_wait=2.0)
    result = client._read_stable_output(
        out,
        poll_interval=0.05,
        stable_for=0.1,
        max_wait=2.0,
        require_json_object=True,
    )
    assert json.loads(result)["summary"] == "完了"
