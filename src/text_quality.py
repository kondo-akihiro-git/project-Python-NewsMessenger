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
