"""要約テキストの機械的ガード。

主たる品質保証は AI レビュー（config/prompts/review.txt）が担う。
本モジュールは、レビューをすり抜けた体言止め（名詞／名詞句だけで終わる行）を
通知前に検出するための保険である。ユーザーの要件は「1行でも体言止め禁止」のため、
述語だと確認できない語尾はすべて体言止めとして扱う（フェイルクローズ）。
"""

from __future__ import annotations

import re

# 文末の句読点等。判定前に取り除く。
_TRAILING_PUNCTUATION = "。、！？!? \t\r\n"

# 述語（用言）で終わっていると判断できる語尾。
# ここに無い語尾は「述語と確認できない」= 体言止めとして扱う。
_PREDICATE_TAIL = re.compile(
    r"(?:"
    r"ませんでした|ました|でした|ます|ません|ましょう|です|ください|"
    r"された|しなかった|した|なった|いた|あった|ある|いる|"
    r"ない|たい|だろう|ろう|だ|た|て|る|む|く|ぐ|ぶ|す"
    r")$"
)


# 記事本文の末尾についてくる関連記事リンクの開始マーカー。
# 本文として要約・レビューに渡すと別記事の事実を混ぜ込む原因になるため除去する。
_RELATED_BLOCK_START = re.compile(
    r"\n(?:"
    # ITmedia の記事末尾フッター。ここ以降は関連記事・アクセスランキング。
    r"Copyright\s+©\s*ITmedia|"
    r"アクセスランキング|"
    r"こんなメディアも見られています|"
    r"関連記事一覧|"
    # ポータルの関連記事見出し。
    r"(?:(?:ITmedia NEWS|Yahoo!ニュース|NHK)\n)?"
    r"(?:【(?:注目|話題|要チェック|関連|おすすめ|新着|注目記事)】|"
    r"1\s+\S.{10,}\s+\d{1,2}/\d{1,2})"
    r")"
)


def trim_related_articles(text: str) -> str:
    """関連記事一覧など、本文以外の末尾ブロックを切り捨てる。"""
    match = _RELATED_BLOCK_START.search(text)
    if match:
        return text[: match.start()].strip()
    return text


# オンデバイスモデルは AI レビューを通過させてしまうことがあるため、
# 「6要素・最低文字数」を機械でも確認する（満たせなければフェイルクローズ）。
_TITLE_TOKEN = re.compile(r"[A-Za-z0-9]{2,}|[ァ-ヶー]{2,}|[一-龥]{2,}")
_STOP_TOKENS = {
    "NEWS",
    "ITmedia",
    "AI",
    "この",
    "その",
    "注意",
    "まとめ",
    "わかった",
    "例も",
    "写真",
    "動画",
}
def title_keywords(title: str) -> tuple[str, ...]:
    """見出しから主体・出来事名の手掛かりになる語を取り出す。"""
    return tuple(
        token
        for token in _TITLE_TOKEN.findall(title or "")
        if len(token) >= 2 and token not in _STOP_TOKENS
    )


# 実機で確認した不自然な述語。自然な日本語とは言い切れないため不合格にする。
_UNNATURAL_PREDICATE = re.compile(r"としました|をしました|ましたです")

# 「信頼が揺らぐ」のような評価だけを書いた要約を機械でも止める。
# 背景イベント（具体的な事象・数値）が要約内に無い場合は根拠なしとして不合格。
_VAGUE_EVALUATION = re.compile(
    r"信頼が揺ら|信頼を失|評価が下が|批判が高ま|不信感が広が|"
    r"混乱が広が|問題が深刻|影響が大き|懸念が広が|動揺が広が"
)
_CONCRETE_BASIS = re.compile(
    r"\d|障害|事故|漏えい|停止|発表|提供|発覚|判明|値下げ|値上げ|"
    r"逮捕|起訴|処分|措置|変更|開始|終了|発生|延期|撤回"
)

# 小学生が初見の用語。記事本文由来の「という」説明が近くに無ければ不合格。
_UNFAMILIAR_TERMS = (
    (r"5G\s?SA", "5G SA"),
    (r"5G\s?NSA", "5G NSA"),
    (r"dots", "dots"),
    (r"APIルーター", "APIルーター"),
    (r"コードレッド", "コードレッド"),
    (r"シャドーAPI", "シャドーAPI"),
    (r"ahamo", "ahamo"),
)

# 役職だけでは WHO を満たさない。固有名詞が直前にあることを要求する。
_PERSON_ROLES = re.compile(r"総務相|首相|官房長官|大臣|知事|社長|会長|CEO|担当相")
_NAME_CHAR = re.compile(r"[一-龥ァ-ヶA-Za-z0-9]")
_WRONG_ROLE_SUFFIX = re.compile(r"[一-龥]{2,4}総務省")

# WHO/WHAT が抜けた「原因究明を求めました」型の表現。
# 何についての究明・調査・対応かが要約内に無ければ不合格。
_VAGUE_ACTION = re.compile(r"原因究明|原因の究明|調査|検証|説明を求め|対応を求め")
_CONCRETE_OBJECT = re.compile(
    r"通信|障害|事故|漏えい|停止|発表|提供|料金|サービス|システム|"
    r"データ|情報|製品|工事|設備|売上|業績"
)


def has_unnatural_predicate(summary: str) -> bool:
    """不自然な述語表現があれば True を返す。"""
    return bool(_UNNATURAL_PREDICATE.search(summary or ""))


def has_vague_evaluation(summary: str) -> bool:
    """根拠のない評価表現が含まれていれば True を返す。"""
    text = summary or ""
    if not _VAGUE_EVALUATION.search(text):
        return False
    # 評価の裏付けとなる具体的なイベント・数値が要約内にあれば機械ガードは通す。
    # ただし最終判断は AI レビュー（concrete_basis_ok）が行う。
    return not bool(_CONCRETE_BASIS.search(text))


def missing_who_what(summary: str) -> bool:
    """「何の原因を究明するのか」など、WHO/WHAT が不明なら True を返す。

    対象は同じ文（。！？で区切った段落）の中になければならない。
    前の文に障害名があっても「原因究明を求めました」だけの文は不合格。
    """
    text = summary or ""
    for segment in re.split(r"[。！？\n]", text):
        if _VAGUE_ACTION.search(segment) and not _CONCRETE_OBJECT.search(segment):
            return True
    return False


# 独立した出来事が2つ以上あるかを機械でも確認する。
# 2行目は補足でも、別主体が別の述語で行動すると「主要イベント1つ」に反する。
_EVENT_PREDICATE = re.compile(r"しました|ます|です|した|なった|発生しました")


def has_multiple_events(summary: str) -> bool:
    """主体＋述語を持つ独立した文が2つ以上あれば True を返す。"""
    segments = [
        segment
        for segment in re.split(r"[。！？]", summary or "")
        if segment.strip()
    ]
    event_segments = [
        segment
        for segment in segments
        if ("は" in segment or "が" in segment) and _EVENT_PREDICATE.search(segment)
    ]
    return len(event_segments) >= 2


def unexplained_terms(summary: str) -> tuple[str, ...]:
    """説明（「という」/「とは」）が近くに無い初見用語を返す。"""
    text = summary or ""
    found: list[str] = []
    for pattern, label in _UNFAMILIAR_TERMS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            tail = text[match.end() : match.end() + 16]
            if "という" in tail or "とは" in tail:
                break
            found.append(label)
            break
    return tuple(found)


def has_role_without_name(summary: str) -> bool:
    """役職だけ書かれていて固有名詞が無い、または誤った役職表記なら True。"""
    text = summary or ""
    if _WRONG_ROLE_SUFFIX.search(text):
        return True
    for match in _PERSON_ROLES.finditer(text):
        previous = text[match.start() - 1] if match.start() > 0 else ""
        if not _NAME_CHAR.fullmatch(previous or ""):
            return True
    return False


def missing_required_elements(
    summary: str, title: str, *, min_chars: int = 35, max_lines: int = 2
) -> tuple[str, ...]:
    """機械的に確認できる必須要素の不足を返す（空なら合格）.

    AI レビューは6要素・4W1H・単一イベントの最終判断を担うが、
    オンデバイスモデルが「合格」と誤判定することがある。最低文字数・
    見出しの固有語2つ（主体と出来事名の手掛かり）・不自然な述語・
    行数・根拠のない評価・WHO/WHAT の欠落は機械でも確認し、欠けて
    いれば通知しない。理由／目的は記事に明記がない場合もあるため、
    AI レビューに委ねる。
    """
    text = (summary or "").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    missing: list[str] = []
    if len(text.replace("\n", "")) < min_chars:
        missing.append("最低文字数")
    if len(lines) > max_lines:
        missing.append(f"{max_lines}行以内")
    keywords = title_keywords(title)
    matched = [token for token in keywords if token in text]
    # 見出しの固有語が1つだけ（用語のみなど）では主体が抜けやすい。
    # 主体と出来事名を機械でも確認するため、2つ以上の一致を要求する。
    if len(matched) < 2:
        missing.append("主体・出来事名")
    if has_unnatural_predicate(text):
        missing.append("不自然な述語")
    if has_vague_evaluation(text):
        missing.append("具体性のない評価")
    if missing_who_what(text):
        missing.append("WHO/WHAT（何についてか）")
    terms = unexplained_terms(text)
    if terms:
        missing.append("初見用語の説明: " + "、".join(terms))
    if has_role_without_name(text):
        missing.append("WHO（固有名詞の主体）")
    if has_multiple_events(text):
        missing.append("単一イベント")
    return tuple(missing)


def has_taigen_dome(summary: str) -> bool:
    """要約のいずれかの行が体言止め（述語で終わらない行）なら True を返す。"""
    for line in summary.splitlines():
        stripped = line.strip().rstrip(_TRAILING_PUNCTUATION)
        if not stripped:
            continue
        if not _PREDICATE_TAIL.search(stripped):
            # 「〜宣言」「〜目標」「〜可能性」など、名詞／名詞句で終わる行。
            # 未知の語尾も述語と証明できないため不合格側に倒す。
            return True
    return False
