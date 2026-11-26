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
    r"(?:ITmedia NEWS|Yahoo!ニュース|NHK)\n)?"
    r"(?:【(?:注目|話題|要チェック|関連|おすすめ|新着|注目記事)】|"
    r"1\s+\S.{10,}\s+\d{1,2}/\d{1,2})"
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


def has_unnatural_predicate(summary: str) -> bool:
    """不自然な述語表現があれば True を返す。"""
    return bool(_UNNATURAL_PREDICATE.search(summary or ""))


def missing_required_elements(
    summary: str, title: str, *, min_chars: int = 35
) -> tuple[str, ...]:
    """機械的に確認できる必須要素の不足を返す（空なら合格）.

    AI レビューは6要素の最終判断を担うが、オンデバイスモデルが
    「6要素あり」と誤判定することがある。最低文字数・見出しの固有語2つ
    （主体と出来事名の手掛かり）・不自然な述語は機械でも確認し、欠けて
    いれば通知しない。理由／目的は記事に明記がない場合もあるため、
    AI レビューに委ねる。
    """
    text = (summary or "").strip()
    missing: list[str] = []
    if len(text.replace("\n", "")) < min_chars:
        missing.append("最低文字数")
    keywords = title_keywords(title)
    matched = [token for token in keywords if token in text]
    # 見出しの固有語が1つだけ（用語のみなど）では主体が抜けやすい。
    # 主体と出来事名を機械でも確認するため、2つ以上の一致を要求する。
    if len(matched) < 2:
        missing.append("主体・出来事名")
    if has_unnatural_predicate(text):
        missing.append("不自然な述語")
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
