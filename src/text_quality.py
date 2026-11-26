"""要約テキストの機械的ガード。

主たる品質保証は AI レビュー（config/prompts/review.txt）が担う。
本モジュールは、レビューをすり抜けた体言止め（名詞／名詞句だけで終わる行）を
通知前に検出するための保険である。誤検出時はフェイルクローズ（再生成または
通知しない）方向に働く。
"""

from __future__ import annotations

import re

# よくある体言止めの語尾。網羅を目指すものではなく、実測で出やすい表現を優先する。
_NOMINAL_ENDINGS = (
    "目標",
    "可能性",
    "影響",
    "原因",
    "対策",
    "方針",
    "見通し",
    "予定",
    "報告",
    "計画",
    "対応",
    "検討",
    "発表",
    "決定",
    "問題",
    "障害",
    "漏えい",
    "事故",
    "変更",
    "修正",
    "強化",
    "見直し",
    "開始",
    "終了",
    "支援",
    "導入",
    "取組",
    "取り組み",
    "確認",
    "警告",
    "呼びかけ",
    "要望",
    "依頼",
    "疑惑",
)

# 述語（用言）で終わっていると判断できる語尾。
_PREDICATE_TAIL = re.compile(
    r"(?:です|ます|ました|でした|した|された|なった|いる|いた|ある|あった|"
    r"た|て|る|む|く|ぐ|ぶ|す|ない)$"
)


def has_taigen_dome(summary: str) -> bool:
    """要約のいずれかの行が体言止めなら True を返す。"""
    for line in summary.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _PREDICATE_TAIL.search(stripped):
            continue
        if stripped.endswith(_NOMINAL_ENDINGS):
            return True
    return False
