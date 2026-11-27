"""macOSショートカット経由で Apple Intelligence を呼び出す AI クライアント."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.text_quality import (
    has_multiple_events,
    has_role_without_name,
    has_taigen_dome,
    has_unnatural_predicate,
    has_vague_evaluation,
    is_bare_announcement,
    missing_required_elements,
    missing_who_what,
    unexplained_terms,
)

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_LEADING_LABEL_PATTERN = re.compile(r"^(?:要約|サマリー|summary)\s*[:：]\s*", re.IGNORECASE)
_RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "quota",
    "too many requests",
    "utilization",
    "利用制限",
    "上限",
    "制限に達し",
    # macOS 側で Apple Intelligence が混雑したときの実機メッセージ。
    "現在、リクエストを完了できません",
    "request could not be completed",
)


# 同じ失敗を繰り返さないよう、再生成ごとに文構造の指示を変える。
# 品質基準を満たすまで再生成を続けるため、単調な再試行は同じ要約を生みがち。
_RETRY_VARIATIONS = (
    "前回と異なる文構造で書いてください。同じ失敗を繰り返さないでください。",
    "主体と出来事を1文にまとめ、必ず述語で終えてください。",
    "記事に明記された事実だけを残し、35〜60文字に収めてください。",
    "主要ニュースだけを1文で書いてください（改行なし）。",
)


# オンデバイスモデルは自由形式 JSON を崩しやすいため、ガイド付き生成で形を固定する。
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["OK", "INSUFFICIENT"]},
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "location": {"type": "string"},
        "numbers": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status",
        "summary",
        "key_facts",
        "reason",
        "location",
        "numbers",
        "uncertainties",
    ],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["PASS", "FAIL"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "missing_facts": {"type": "array", "items": {"type": "string"}},
        "corrections": {"type": "array", "items": {"type": "string"}},
        "keyword_explanation_ok": {"type": "boolean"},
        "unexplained_terms": {"type": "array", "items": {"type": "string"}},
        "concrete_basis_ok": {"type": "boolean"},
        "four_w_one_h_ok": {"type": "boolean"},
        "single_event_ok": {"type": "boolean"},
        "missing_4w1h": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": [
        "status",
        "issues",
        "unsupported_claims",
        "missing_facts",
        "corrections",
        "keyword_explanation_ok",
        "unexplained_terms",
        "concrete_basis_ok",
        "four_w_one_h_ok",
        "single_event_ok",
        "missing_4w1h",
        "reason",
    ],
}


class AIClientError(Exception):
    """AI処理における基底エラー."""


class ShortcutNotFoundError(AIClientError):
    """指定されたショートカットが見つからないエラー."""


class AIRateLimitError(AIClientError):
    """Apple Intelligence の利用制限に達したエラー."""


class AITimeoutError(AIClientError):
    """ショートカット実行がタイムアウトしたエラー."""


class InvalidAIResponseError(AIClientError):
    """AIのJSON出力が不正または検証基準を満たさないエラー."""


class SummaryQualityError(InvalidAIResponseError):
    """要約が品質基準を満たさないエラー（再生成の対象）."""


@dataclass(frozen=True)
class SummaryResult:
    """要約AIの構造化出力."""

    status: str  # "OK" | "INSUFFICIENT"
    summary: str
    key_facts: tuple
    reason: str
    location: str
    numbers: tuple
    uncertainties: tuple
    raw_text: str = ""


@dataclass(frozen=True)
class ReviewResult:
    """レビューAIの構造化出力."""

    status: str  # "PASS" | "FAIL"
    issues: tuple
    unsupported_claims: tuple
    missing_facts: tuple
    corrections: tuple
    reason: str
    raw_text: str = ""
    # 小学生が初見のキーワードに記事本文ベースの説明があるか。False なら不合格。
    keyword_explanation_ok: bool = True
    unexplained_terms: tuple = ()
    # 曖昧表現に裏付けがあるか、4W1Hが揃っているか、主要ニュースが1つか。
    concrete_basis_ok: bool = True
    four_w_one_h_ok: bool = True
    single_event_ok: bool = True
    missing_4w1h: tuple = ()


def render_prompt(template_path, variables):
    """{{KEY}} プレースホルダを安全に置換してプロンプト文字列を生成する."""
    text = Path(template_path).read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def repair_json_string_control_chars(text):
    """JSON文字列内の生の改行等をエスケープして解析可能にする.

    Apple Intelligence は JSON の文字列値に改行をそのまま埋め込むことがある。
    そのままでは json.loads が失敗するため、文字列内のみを安全にエスケープする。
    """
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            if ch in _CONTROL_ESCAPES:
                out.append(_CONTROL_ESCAPES[ch])
                continue
            if ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
                continue
            out.append(ch)
            continue
        out.append(ch)
        if ch == '"':
            in_string = True
    return "".join(out)


def _looks_like_complete_json_object(text):
    """テキストが完全な JSON オブジェクトらしいかを簡易検査する."""
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    depth = 0
    in_string = False
    escaped = False
    for ch in stripped:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def extract_json_object(raw_text):
    """AI応答テキストから JSON オブジェクトを抽出してパースする."""
    if not raw_text or not raw_text.strip():
        raise InvalidAIResponseError("AIの応答が空です。")

    cleaned = raw_text.strip()
    fence_match = _CODE_FENCE_PATTERN.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    candidates = [cleaned]
    repaired = repair_json_string_control_chars(cleaned)
    if repaired != cleaned:
        candidates.append(repaired)
    # Apple Intelligence が文字列の閉じ引用符を日本語の鉤括弧で出力することがある。
    # 区切り記号の直前だけを ASCII 引用符に置き換えた候補も試す（検証は後段で厳密に行う）。
    japanese_quote_repaired = re.sub(r"」(?=\s*[,}\]])", '"', cleaned)
    if japanese_quote_repaired != cleaned and japanese_quote_repaired not in candidates:
        candidates.append(japanese_quote_repaired)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 最後の手段: Apple Intelligence が status/summary 直後に出力を切り上げた場合。
    # 事実を補うのではなく、欠落メタフィールドを「不明」/空として明示的に埋める。
    # 要約本文はAIの実出力を保持し、事実確認は後段のレビューAIが行う。
    # 体言止め・文字数・空文字などの検査は validate_summary_payload が行うため、
    # この復旧だけで通知が通るわけではない（フェイルクローズは維持）。
    truncated = re.match(
        r'^\s*\{\s*"status"\s*:\s*"(OK|INSUFFICIENT)"\s*,\s*"summary"\s*:\s*"'
        r'((?:[^"\\]|\\.)*?)"\s*,?\s*$',
        cleaned,
        re.DOTALL,
    )
    if truncated:
        status, summary = truncated.group(1), truncated.group(2)
        try:
            summary = json.loads(f'"{summary}"')
        except json.JSONDecodeError:
            summary = summary.replace("\\n", "\n")
        # 要約が空の切り上げは復旧しない（validate前にフェイルクローズ）。
        if summary.strip():
            candidates.append(
                json.dumps(
                    {
                        "status": status,
                        "summary": summary,
                        "key_facts": [],
                        "reason": "不明",
                        "location": "不明",
                        "numbers": [],
                        "uncertainties": ["AI出力が途中で終了したため詳細は不明"],
                    },
                    ensure_ascii=False,
                )
            )

    decoder = json.JSONDecoder()
    for candidate in candidates:
        for idx, ch in enumerate(candidate):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(candidate[idx:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue

    raise InvalidAIResponseError(f"JSONオブジェクトとして解析できません: {cleaned[:200]}")


def _ensure_str_list(data, key):
    if key not in data:
        raise InvalidAIResponseError(f"必須フィールド '{key}' が不足しています。")
    val = data[key]
    if not isinstance(val, list):
        raise InvalidAIResponseError(f"フィールド '{key}' は配列である必要があります。")
    return tuple(str(item).strip() for item in val if str(item).strip())


def _ensure_bool(data, key):
    if key not in data:
        raise InvalidAIResponseError(f"必須フィールド '{key}' が不足しています。")
    val = data[key]
    if not isinstance(val, bool):
        raise InvalidAIResponseError(f"フィールド '{key}' は真偽値である必要があります。")
    return val


def _ensure_str(data, key):
    if key not in data:
        raise InvalidAIResponseError(f"必須フィールド '{key}' が不足しています。")
    val = data[key]
    # Apple Intelligence が文字列ではなく文字列配列を返すことがある。
    # 事実は増やさず、既存の文字列を連結するだけなら安全に正規化できる。
    if isinstance(val, list):
        val = "、".join(str(item).strip() for item in val if str(item).strip())
    if not isinstance(val, str):
        raise InvalidAIResponseError(f"フィールド '{key}' は文字列である必要があります。")
    return val.strip()


# 意味を変えずに短くできる表現だけを対象にする。
# 「ました」→「した」は「発生しした」「求めした」のような不自然な述語を生むため禁止。
_SAFE_COMPACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![とを])しました"), "した"),
    (re.compile(r"について(?=(?:議論|発表|説明|言及|報告))"), "を"),
    (re.compile(r"に対して"), "に"),
    (re.compile(r"により"), "で"),
    # フィラー表現の短縮（意味を変えない）。
    (re.compile(r"新機能として"), "新機能"),
    (re.compile(r"β版として"), "β版で"),
    (re.compile(r"業界標準のシステム"), "業界標準"),
    (re.compile(r"業界標準に、"), "業界標準に"),
)
_CORPORATE_SUFFIX = re.compile(r"株式会社|\(株\)|（株）")


def _compact_overlong_summary(text: str, max_chars: int) -> str:
    """1〜数文字の超過だけを、意味を変えない言い換えで収める Safety net。

    「発生しました」→「発生した」「〜について議論」→「〜を議論」のように、
    事実・助詞・述語を保ったまま短くなる定型表現だけを置き換える。
    新しい事実は追加せず、置換後も全品質ガードで再検証する。
    安全な置換が無い場合はそのまま返し、再生成側で解消する。
    """
    current = text
    for _ in range(6):
        if len(current.replace("\n", "")) <= max_chars:
            return current
        changed = False
        for pattern, replacement in _SAFE_COMPACTIONS:
            matches = list(pattern.finditer(current))
            if not matches:
                continue
            match = matches[-1]
            current = current[: match.start()] + replacement + current[match.end() :]
            changed = True
            break
        if not changed:
            break
    return current


def validate_summary_payload(
    data, *, max_lines=2, max_chars=60, raw_text="", title="", article_body=""
):
    """要約AIのJSON出力を検査して SummaryResult を返す.

    title が渡された場合は見出しの固有語も機械ガードで確認する。
    品質基準に抵触した場合は SummaryQualityError を投げ、再生成対象とする。
    """
    status = _ensure_str(data, "status").upper()
    if status not in ("OK", "INSUFFICIENT"):
        raise InvalidAIResponseError(f"不正な status です: {status!r}")

    summary = _ensure_str(data, "summary")
    key_facts = _ensure_str_list(data, "key_facts")
    reason = _ensure_str(data, "reason")
    location = _ensure_str(data, "location")
    numbers = _ensure_str_list(data, "numbers")
    uncertainties = _ensure_str_list(data, "uncertainties")

    if status == "OK":
        if not summary:
            raise InvalidAIResponseError("status=OK ですが summary が空です。")
        if _URL_PATTERN.search(summary):
            raise SummaryQualityError("要約本文に URL が含まれています。")
        if _LEADING_LABEL_PATTERN.match(summary):
            raise SummaryQualityError("要約本文に不要な前置きラベルが含まれています。")
        if "{" in summary or "}" in summary or "```" in summary:
            raise SummaryQualityError("要約本文にJSONやコード記法が含まれています。")
        # オンデバイスAIは改行せず「。」で2文目を繋いでくることがある。
        # 文境界を行境界として扱い、超過時に補足行を落とせるようにする。
        sentence_lines = re.sub(r"([。！？])", r"\1\n", summary)
        lines = [line.strip() for line in sentence_lines.splitlines() if line.strip()]
        if not lines:
            raise InvalidAIResponseError("要約本文に有効な行がありません。")
        normalized_summary = _compact_overlong_summary("\n".join(lines), max_chars)
        char_count = len(normalized_summary.replace("\n", ""))
        # 60文字超過の大半は、主要ニュースに重複・別記事由来の
        # 補足文が付いたことが原因。1文だけで全ガードを満たせるなら補足文を落とす。
        # 補足文の削除は「補足のみ」というルールに沿い、6要素はAIレビューで確認する。
        supplement_dropped = False
        if char_count > max_chars and len(lines) >= 2:
            first_line = lines[0].strip()
            first_ok = (
                35 <= len(first_line) <= max_chars
                and not has_taigen_dome(first_line)
                and not has_unnatural_predicate(first_line)
                and not has_vague_evaluation(first_line)
                and not missing_who_what(first_line)
                and not unexplained_terms(first_line)
                and not has_role_without_name(first_line)
                and not has_multiple_events(first_line)
                # 見出し固有語2つは要約全体の健全性チェック。補足行を落とす際は
                # 2行目にしか無い固有語（例: 「メッセージ内容」）があるため例外にし、
                # 主体・出来事名の最終判定はAIレビューへ委ねる。
                and not [
                    item
                    for item in missing_required_elements(
                        first_line, title, min_chars=35, max_lines=2
                    )
                    if item != "主体・出来事名"
                ]
            )
            if first_ok:
                normalized_summary = first_line
                char_count = len(first_line)
                supplement_dropped = True
        if len(lines) > max_lines and not supplement_dropped:
            raise SummaryQualityError(
                f"要約が {max_lines} 行を超えています ({len(lines)} 行)。"
            )
        if char_count > max_chars:
            raise SummaryQualityError(
                f"要約が上限文字数 ({max_chars}文字) を超えています ({char_count}文字): "
                f"{normalized_summary!r}"
            )
        if has_taigen_dome(normalized_summary):
            raise SummaryQualityError(
                "要約に体言止め（名詞で終わる行）が含まれています: "
                f"{normalized_summary!r}"
            )
        if has_unnatural_predicate(normalized_summary):
            raise SummaryQualityError(
                "要約に不自然な述語が含まれています: " f"{normalized_summary!r}"
            )
        if _CORPORATE_SUFFIX.search(normalized_summary) and not _CORPORATE_SUFFIX.search(
            f"{title}\n{article_body}"
        ):
            raise SummaryQualityError(
                "要約に記事本文・見出しにない法人格表記（株式会社／(株)）があります。"
            )
        if has_vague_evaluation(normalized_summary):
            raise SummaryQualityError(
                "要約に具体的な根拠のない評価表現が含まれています: "
                f"{normalized_summary!r}"
            )
        if missing_who_what(normalized_summary):
            raise SummaryQualityError(
                "要約に WHO/WHAT（誰が・何について）が不足しています: "
                f"{normalized_summary!r}"
            )
        terms = unexplained_terms(normalized_summary)
        if terms:
            raise SummaryQualityError(
                "要約に説明のない初見用語があります: " + "、".join(terms)
            )
        if has_role_without_name(normalized_summary):
            raise SummaryQualityError(
                "要約に固有名詞のない役職表現があります: "
                f"{normalized_summary!r}"
            )
        if has_multiple_events(normalized_summary):
            raise SummaryQualityError(
                "要約に独立した複数イベントが含まれています: "
                f"{normalized_summary!r}"
            )
        if is_bare_announcement(normalized_summary):
            raise SummaryQualityError(
                "要約が出来事の発表だけで終わり、理由・対応・目的が不足しています: "
                f"{normalized_summary!r}"
            )
        if len(normalized_summary.replace("\\n", "")) < 35:
            raise SummaryQualityError(
                f"要約が短すぎます（35文字未満）: {normalized_summary!r}"
            )
        if title:
            missing = missing_required_elements(
                normalized_summary, title, min_chars=35, max_lines=max_lines
            )
            if supplement_dropped:
                # 削除した補足行にしか無い見出し語は要求しない。AIレビューが
                # 主体・出来事名の充足を最終判断する。
                missing = tuple(item for item in missing if item != "主体・出来事名")
            if missing:
                raise SummaryQualityError(
                    "要約に必須要素が不足しています: " + "、".join(missing)
                )
        summary = normalized_summary

    return SummaryResult(
        status=status,
        summary=summary,
        key_facts=key_facts,
        reason=reason,
        location=location,
        numbers=numbers,
        uncertainties=uncertainties,
        raw_text=raw_text,
    )


def validate_review_payload(data, *, raw_text=""):
    """レビューAIのJSON出力を検査して ReviewResult を返す."""
    status = _ensure_str(data, "status").upper()
    if status not in ("PASS", "FAIL"):
        raise InvalidAIResponseError(f"不正なレビュー status です: {status!r}")

    issues = _ensure_str_list(data, "issues")
    unsupported_claims = _ensure_str_list(data, "unsupported_claims")
    missing_facts = _ensure_str_list(data, "missing_facts")
    corrections = _ensure_str_list(data, "corrections")
    unexplained_terms = _ensure_str_list(data, "unexplained_terms")
    reason = _ensure_str(data, "reason")

    keyword_explanation_ok = _ensure_bool(data, "keyword_explanation_ok")
    concrete_basis_ok = _ensure_bool(data, "concrete_basis_ok")
    four_w_one_h_ok = _ensure_bool(data, "four_w_one_h_ok")
    single_event_ok = _ensure_bool(data, "single_event_ok")
    missing_4w1h = _ensure_str_list(data, "missing_4w1h")

    # AIがPASSと判定しても、以下が応答されていれば安全側に倒して不合格にする。
    forced_issues: list[str] = []
    if not keyword_explanation_ok:
        forced_issues.append("初見キーワードの説明が要約にありません。")
    if not concrete_basis_ok:
        forced_issues.append("具体的な裏付けのない評価表現があります。")
    if not four_w_one_h_ok or missing_4w1h:
        detail = "、".join(missing_4w1h) if missing_4w1h else "WHO/WHAT など"
        forced_issues.append(f"4W1Hが不足しています: {detail}")
    if not single_event_ok:
        forced_issues.append("主要ニュースが1つに絞られていません。")
    if forced_issues:
        status = "FAIL"
        issues = issues + tuple(forced_issues)
        reason = (reason + " " + " ".join(forced_issues)).strip()

    return ReviewResult(
        status=status,
        issues=issues,
        unsupported_claims=unsupported_claims,
        missing_facts=missing_facts,
        corrections=corrections,
        reason=reason,
        raw_text=raw_text,
        keyword_explanation_ok=keyword_explanation_ok,
        unexplained_terms=unexplained_terms,
        concrete_basis_ok=concrete_basis_ok,
        four_w_one_h_ok=four_w_one_h_ok,
        single_event_ok=single_event_ok,
        missing_4w1h=missing_4w1h,
    )


# ---------------------------------------------------------------------------
# ShortcutAIClient
# ---------------------------------------------------------------------------

# テスト時に差し替えられるよう Popen を差し替え可能にする型
PopenFactory = Callable[..., subprocess.Popen]


class ShortcutAIClient:
    """macOS の `shortcuts run` を介して Apple Intelligence を実行するクライアント."""

    def __init__(
        self,
        *,
        summarize_shortcut: str = "News_Summarize",
        review_shortcut: str = "News_Review",
        shortcuts_bin: str = "/usr/bin/shortcuts",
        timeout_seconds: float = 240.0,
        # 実測で2回では55文字に届かないことが多いため、具体的な短縮指示付きで最大5回試行する。
        max_json_retries: int = 4,
        # Apple Intelligence の連続呼び出しは失敗しやすいため、再試行間隔を長めに取る。
        retry_delay_seconds: float = 5.0,
        # list_shortcuts 専用: subprocess.run 互換の呼び出し可能オブジェクト
        list_runner: Optional[Callable] = None,
        # run_shortcut 専用: subprocess.Popen 互換の呼び出し可能オブジェクト
        popen_factory: Optional[PopenFactory] = None,
        logger: Optional[logging.Logger] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        # ファイルポーリングパラメータ
        poll_interval: float = 0.3,
        stable_for: float = 2.0,
        max_wait: float = 180.0,
    ) -> None:
        self._summarize_shortcut = summarize_shortcut
        self._review_shortcut = review_shortcut
        self._shortcuts_bin = shortcuts_bin
        self._timeout = timeout_seconds
        self._max_json_retries = max_json_retries
        self._retry_delay = retry_delay_seconds
        self._list_runner = list_runner or subprocess.run
        self._popen_factory = popen_factory or subprocess.Popen
        self._logger = logger or logging.getLogger("news_telegram.ai")
        self._sleep = sleep_fn
        self._poll_interval = poll_interval
        self._stable_for = stable_for
        self._max_wait = max_wait

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def list_shortcuts(self) -> list:
        """登録されているショートカット名一覧を取得する."""
        try:
            proc = self._list_runner(
                [self._shortcuts_bin, "list"],
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AITimeoutError("shortcuts list がタイムアウトしました。") from exc
        except OSError as exc:
            raise AIClientError(f"shortcuts コマンドを実行できません: {exc}") from exc

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise AIClientError(f"shortcuts list 失敗 (code={proc.returncode}): {err}")

        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    # ------------------------------------------------------------------
    # Core shortcut runner (Popen + output-file polling)
    # ------------------------------------------------------------------

    def run_shortcut(self, shortcut_name: str, prompt_text: str) -> str:
        """指定したショートカットに入力テキストを渡し、出力テキストを返す.

        Apple Intelligence はプロセス終了後も非同期で出力ファイルに書き込みを
        続けることがあるため、subprocess.Popen を使用し、プロセス終了後に
        ファイルサイズが安定するまでポーリングしてから読み込む。
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="news_ai_"))
        try:
            input_file = tmp_dir / "input.txt"
            output_file = tmp_dir / "output.txt"
            input_file.write_text(prompt_text, encoding="utf-8")

            cmd = [
                self._shortcuts_bin,
                "run",
                shortcut_name,
                "--input-path",
                str(input_file),
                "--output-path",
                str(output_file),
            ]

            # --- プロセス起動 ---
            try:
                proc = self._popen_factory(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError as exc:
                raise AIClientError(f"shortcuts 実行エラー: {exc}") from exc

            # --- プロセス終了待機 ---
            try:
                stdout_data, stderr_data = proc.communicate(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise AITimeoutError(
                    f"ショートカット '{shortcut_name}' が {self._timeout:.0f} 秒でタイムアウトしました。"
                )

            # --- 終了コード確認 ---
            if proc.returncode != 0:
                err_msg = (stderr_data or stdout_data or "").strip()
                lower = err_msg.lower()
                if "couldn't find" in lower or "見つかりません" in err_msg:
                    raise ShortcutNotFoundError(
                        f"ショートカット '{shortcut_name}' が見つかりません: {err_msg}"
                    )
                if any(kw in lower or kw in err_msg for kw in _RATE_LIMIT_KEYWORDS):
                    raise AIRateLimitError(
                        f"Apple Intelligence の利用制限に到達した可能性があります: {err_msg}"
                    )
                raise AIClientError(
                    f"ショートカット '{shortcut_name}' 実行失敗 (code={proc.returncode}): {err_msg}"
                )

            # --- 出力ファイルをポーリングして安定したら読み込む ---
            out_text = self._read_stable_output(
                output_file,
                poll_interval=self._poll_interval,
                stable_for=self._stable_for,
                max_wait=self._max_wait,
                require_json_object=True,
            )
            # プロセス終了後も「モデルを使用」の出力ファイルへの書き込みが続くことがある。
            # 不完全なJSONのまま返さず、追加で待って完全なオブジェクトを取得する。
            if out_text and not _looks_like_complete_json_object(out_text):
                self._logger.warning(
                    "ショートカット '%s' の出力が不完全なため追加待機します。", shortcut_name
                )
                extended = self._read_stable_output(
                    output_file,
                    poll_interval=self._poll_interval,
                    stable_for=self._stable_for,
                    max_wait=self._max_wait,
                    require_json_object=True,
                )
                if extended:
                    out_text = extended
            if out_text:
                return out_text

            # フォールバック: stdout に出力されていれば使用
            if stdout_data and stdout_data.strip():
                return stdout_data.strip()

            raise InvalidAIResponseError(
                f"ショートカット '{shortcut_name}' から出力を取得できませんでした。"
            )

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _read_stable_output(
        self,
        path: Path,
        *,
        poll_interval: float = 0.3,
        stable_for: float = 0.6,
        max_wait: float = 5.0,
        require_json_object: bool = False,
    ) -> str:
        """出力ファイルのサイズが安定するまでポーリングしてからテキストを返す.

        Apple Intelligence はプロセス終了後も非同期で出力ファイルに書き込み続ける
        ことがある。ファイルサイズが `stable_for` 秒間変化しなくなった時点で
        読み込みを確定する。`require_json_object=True` の場合は、さらに完全な
        JSON オブジェクトに見えるまで待機する（途中まで書き込まれた JSON を
        誤って採用しないため）。`max_wait` 秒経過した時点の内容を返す。
        """
        deadline = time.monotonic() + max_wait
        prev_size = -1
        stable_elapsed = 0.0
        last_check = time.monotonic()

        while time.monotonic() < deadline:
            if not path.exists():
                self._sleep(poll_interval)
                stable_elapsed = 0.0
                last_check = time.monotonic()
                continue

            cur_size = path.stat().st_size
            now = time.monotonic()
            elapsed_since_last = now - last_check

            if cur_size == prev_size and cur_size > 0:
                stable_elapsed += elapsed_since_last
                if stable_elapsed >= stable_for:
                    if not require_json_object:
                        break
                    current_text = path.read_text(encoding="utf-8", errors="replace")
                    if _looks_like_complete_json_object(current_text):
                        break
            else:
                stable_elapsed = 0.0

            prev_size = cur_size
            last_check = now
            self._sleep(poll_interval)

        if not path.exists() or path.stat().st_size == 0:
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()

    # ------------------------------------------------------------------
    # JSON validation loop
    # ------------------------------------------------------------------

    def _run_with_json_validation(
        self,
        shortcut_name: str,
        prompt_text: str,
        validator: Callable,
        *,
        max_chars: int = 60,
        retry_template: Optional[str] = None,
    ) -> Any:
        attempts = self._max_json_retries + 1
        last_exc = None
        retry_prompt = prompt_text
        previous_summary = ""

        def compact_retry(
            instruction: str = "", *, variation: str = ""
        ) -> Optional[str]:
            """短い専用プロンプトへ失敗理由を渡して再生成する。

            オンデバイス模型はコンテキストが4096トークンまでなので、
            長い元プロンプトへ説明を追記すると ContextLengthError になる。
            試行ごとの文構造の変化はテンプレート内の差し込み枠に載せる。
            """
            if not retry_template or not previous_summary:
                return None
            over = len(previous_summary.replace("\n", ""))
            need = max(1, over - max_chars)
            effective = instruction or "前回の要約の問題点を修正してください。"
            if variation and "{{FAILURE_INSTRUCTION}}" in retry_template:
                effective = f"{effective} {variation}".strip()
            return (
                retry_template.replace("{{PREVIOUS_SUMMARY}}", previous_summary)
                .replace("{{OVER_CHARS}}", str(over))
                .replace("{{MAX_CHARS}}", str(max_chars))
                .replace("{{NEED_CHARS}}", str(need))
                .replace("{{FAILURE_INSTRUCTION}}", effective)
            )

        for attempt in range(1, attempts + 1):
            try:
                raw_output = self.run_shortcut(shortcut_name, retry_prompt)
                parsed = extract_json_object(raw_output)
                if isinstance(parsed.get("summary"), str):
                    previous_summary = parsed["summary"]
                return validator(parsed, raw_output)
            except (ShortcutNotFoundError, AIRateLimitError, AITimeoutError):
                # 利用制限・タイムアウトは再生成ではなくフェイルクローズ対象。
                raise
            except InvalidAIResponseError as exc:
                # JSON破損と品質ガード抵触は、短い再生成プロンプトで解消を試みる。
                last_exc = exc
                self._logger.warning(
                    "AI応答検証失敗 (%s 試行 %d/%d): %s",
                    shortcut_name,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    # 文字数・行数超過は同じプロンプトを再実行しても再現するため、
                    # 直前に生成された要約そのものを渡し、事実を残した言い換えを指示する。
                    message = str(exc)
                    variation = _RETRY_VARIATIONS[
                        (attempt - 1) % len(_RETRY_VARIATIONS)
                    ]
                    if "上限文字数" in message or "行を超えています" in message:
                        over_chars = len(previous_summary.replace("\n", ""))
                        need_chars = max(1, over_chars - max_chars)
                        compact = compact_retry(
                            f"前回は{over_chars}文字です。あと{need_chars}文字だけ削って"
                            f"{max_chars}文字ちょうどにしてください。事実・助詞・述語は残してください。",
                            variation=variation,
                        )
                        if compact:
                            retry_prompt = compact
                        else:
                            retry_prompt = prompt_text
                            if previous_summary:
                                over = len(previous_summary.replace("\n", ""))
                                need = max(1, over - max_chars)
                                retry_prompt += (
                                    f"\n\n【再生成指示】前回の要約は{over}文字で上限（{max_chars}文字）を超えました。"
                                    f"あと{need}文字以上を、事実を残した言い換えで削ってください。"
                                    f"前回の要約:\n{previous_summary}\n"
                                    "この要約の主体・出来事・対象・理由・対応・目的と述語をすべて残したまま、"
                                    "言い換えて上限以内に短くしてください。事実を削ってはいけません。"
                                )
                            else:
                                retry_prompt += (
                                    "\n\n【再生成指示】前回の要約は上限を超えていました。"
                                    "6要素と述語を維持したまま、指定の上限以内に短くしてください。"
                                )
                    elif "体言止め" in message:
                        # 名詞止めは行ごとの問題なので、直前の要約を渡して述語の付け方を具体的に指示する。
                        compact = compact_retry(
                            "前回の要約に体言止めがありました。各行を述語（〜しました／〜します／〜です）で終えてください。",
                            variation=variation,
                        )
                        if compact:
                            retry_prompt = compact
                        else:
                            retry_prompt = prompt_text
                            if previous_summary:
                                retry_prompt += (
                                    "\n\n【再生成指示】前回の要約に体言止め（名詞で終わる行）がありました。"
                                    f"前回の要約:\n{previous_summary}\n"
                                    "各行を必ず述語（〜しました／〜します／〜です）で終えてください。"
                                    "6要素と事実は残したまま、述語を付けるために必要なら他の表現を短くしてください。"
                                    "文字数は60文字以内を守ってください。"
                                )
                            else:
                                retry_prompt += (
                                    "\n\n【再生成指示】前回の要約に体言止めがありました。"
                                    "各行を述語で終え、6要素と60文字以内を維持してください。"
                                )
                    elif "初見用語" in message:
                        # 説明なしで略語が出た場合、説明を足すより略語を避ける方が
                        # 60文字制限と両立しやすい。記事本文の意味だけを使わせる。
                        term = message.split(":", 1)[-1].strip()
                        hint = (
                            "記事本文にある意味（例: 5G SA→5G専用設備）だけを使ってください。"
                            if term == "5G SA"
                            else "記事本文にある意味だけを使ってください。"
                        )
                        compact = compact_retry(
                            f"前回の要約は初見用語「{term}」の説明がありません。"
                            f"「{term}」と書かず、{hint}"
                            "主体と出来事、述語、35〜60文字は維持してください。",
                            variation=variation,
                        )
                        if compact:
                            retry_prompt = compact
                        else:
                            retry_prompt = prompt_text
                            if previous_summary:
                                retry_prompt += (
                                    "\n\n【再生成指示】前回の要約に説明のない初見用語がありました。"
                                    f"前回の要約:\n{previous_summary}\n"
                                    f"「{term}」と書かず、{hint}"
                                )
                    elif any(
                        keyword in message
                        for keyword in (
                            "根拠のない評価",
                            "WHO/WHAT",
                            "4W1H",
                            "単一",
                            "複数イベント",
                            "初見用語",
                            "役職",
                        )
                    ):
                        compact = compact_retry(
                            "前回の要約は品質ガードに抵触しました。"
                            "主要ニュース（誰が・何を）を1文で書いてください（改行なし）。"
                            "独立した出来事は1文にまとめ、別主体の行動を別文にしないでください。"
                            "根拠のない評価表現を削り、記事に明記された出来事と数値を入れてください。"
                            "役職だけを書かずに固有名詞を入れ、初見用語には「という」で記事の意味を説明してください。"
                            "6要素・述語・60文字以内は維持してください。",
                            variation=variation,
                        )
                        if compact:
                            retry_prompt = compact
                        else:
                            retry_prompt = prompt_text
                            if previous_summary:
                                retry_prompt += (
                                    "\n\n【再生成指示】前回の要約は品質ガードに抵触しました。"
                                    f"前回の要約:\n{previous_summary}\n"
                                    "主要ニュース（誰が・何を）を1文で書いてください（改行なし）。"
                                    "独立した出来事は1文にまとめ、別主体の行動を別文にしないでください。"
                                    "根拠のない評価表現を削り、記事に明記された出来事と数値を入れてください。"
                                    "役職だけを書かずに固有名詞を入れ、初見用語には「という」で記事の意味を説明してください。"
                                    "6要素・述語・60文字以内は維持してください。"
                                )
                    else:
                        # JSON解析失敗や出力の途切れは、長い再生成指示プロンプト自体が
                        # 原因になることがある。次の試行は元のプロンプトに戻す。
                        retry_prompt = prompt_text
                        previous_summary = ""
                    if self._retry_delay > 0:
                        self._sleep(self._retry_delay)
            except AIClientError:
                # ブリッジ実行失敗など外部障害も再試行しない。
                raise

        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Public AI methods
    # ------------------------------------------------------------------

    def summarize(
        self,
        prompt_text: str,
        *,
        max_lines: int = 2,
        max_chars: int = 60,
        retry_template: Optional[str] = None,
        title: str = "",
        article_body: str = "",
    ) -> SummaryResult:
        """News_Summarize ショートカットを実行して要約結果を返す."""
        return self._run_with_json_validation(
            self._summarize_shortcut,
            prompt_text,
            lambda data, raw: validate_summary_payload(
                data,
                max_lines=max_lines,
                max_chars=max_chars,
                raw_text=raw,
                title=title,
                article_body=article_body,
            ),
            max_chars=max_chars,
            retry_template=retry_template,
        )

    def review(self, prompt_text: str) -> ReviewResult:
        """News_Review ショートカットを実行してレビュー結果を返す."""
        return self._run_with_json_validation(
            self._review_shortcut,
            prompt_text,
            lambda data, raw: validate_review_payload(data, raw_text=raw),
        )

    def revise(
        self,
        prompt_text: str,
        *,
        max_lines: int = 2,
        max_chars: int = 60,
        title: str = "",
        article_body: str = "",
        retry_template: Optional[str] = None,
    ) -> SummaryResult:
        """修正プロンプトを要約ショートカットへ渡し、修正済み要約を返す.

        ショートカットは汎用の「モデルを使用」パススルー構成のため、
        修正要約にも News_Summarize を使用する（別ショートカット不要）。
        """
        return self._run_with_json_validation(
            self._summarize_shortcut,
            prompt_text,
            lambda data, raw: validate_summary_payload(
                data,
                max_lines=max_lines,
                max_chars=max_chars,
                raw_text=raw,
                title=title,
                article_body=article_body,
            ),
            max_chars=max_chars,
            retry_template=retry_template,
        )


class OnDeviceAIClient(ShortcutAIClient):
    """apple_llm ブリッジ経由でオンデバイスの Apple Intelligence を呼び出す.

    macOS ショートカットの Cloud モデルは利用制限で失敗することがあるため、
    Apple Silicon 上のオンデバイスモデルを直接使う。外部の有料 API は使わない。
    検証・リトライ・フェイルクローズのロジックは ShortcutAIClient を再利用する。
    """

    def __init__(
        self,
        *,
        bridge_python: str = "/usr/bin/python3",
        bridge_script: Optional[Path] = None,
        timeout_seconds: float = 300.0,
        max_json_retries: int = 6,
        retry_delay_seconds: float = 1.0,
        logger: Optional[logging.Logger] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        runner: Optional[Callable] = None,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_json_retries=max_json_retries,
            retry_delay_seconds=retry_delay_seconds,
            logger=logger,
            sleep_fn=sleep_fn,
        )
        self._bridge_python = bridge_python
        self._bridge_script = (
            Path(bridge_script)
            if bridge_script is not None
            else Path(__file__).resolve().parents[1] / "scripts" / "apple_llm_bridge.py"
        )
        self._runner = runner or subprocess.run

    def run_shortcut(self, shortcut_name: str, prompt_text: str) -> str:
        """ショートカット名で出力形式を選び、オンデバイスブリッジへプロンプトを渡す。"""
        # pyenv の Python は x86_64 (Rosetta) の場合があるため、必ず arm64 で起動する。
        cmd = ["/usr/bin/arch", "-arm64", self._bridge_python, str(self._bridge_script)]
        schema = REVIEW_SCHEMA if shortcut_name == self._review_shortcut else SUMMARY_SCHEMA
        cmd.extend(
            ["--schema", json.dumps(schema, ensure_ascii=False)]
        )
        try:
            proc = self._runner(
                cmd,
                input=prompt_text,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AITimeoutError(
                f"オンデバイスAIが {self._timeout:.0f} 秒でタイムアウトしました。"
            ) from exc
        except OSError as exc:
            raise AIClientError(f"オンデバイスAIブリッジを実行できません: {exc}") from exc

        err_msg = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode != 0:
            lower = err_msg.lower()
            if any(kw in lower or kw in err_msg for kw in _RATE_LIMIT_KEYWORDS):
                raise AIRateLimitError(
                    f"Apple Intelligence の利用制限に到達した可能性があります: {err_msg}"
                )
            raise AIClientError(f"オンデバイスAI実行失敗 (code={proc.returncode}): {err_msg}")

        output = (proc.stdout or "").strip()
        if not output:
            raise InvalidAIResponseError("オンデバイスAIから出力を取得できませんでした。")
        return output
