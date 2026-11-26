"""Apple Intelligence が混雑したときのクールダウン管理。

有料APIへのフォールバックは行わず、一定時間だけ AI 呼び出しを止める。
状態は data/ 配下の JSON に保存し、Git 管理外に置く。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def cooldown_path(project_root: Path) -> Path:
    return project_root / "data" / "ai_cooldown.json"


def remaining_seconds(project_root: Path, now: Optional[float] = None) -> float:
    """クールダウン残り秒数を返す（0なら呼び出し可能）。"""
    path = cooldown_path(project_root)
    if not path.exists():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        until = float(payload.get("until", 0))
    except (OSError, ValueError, TypeError):
        # 状態ファイルが壊れていても安全側（少し待つ）に倒す。
        return 60.0
    current = time.time() if now is None else now
    return max(0.0, until - current)


def start(project_root: Path, seconds: float, reason: str = "") -> None:
    """クールダウンを開始／延長する。"""
    path = cooldown_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = time.time()
    # 再検出時は残り時間に追加する（連続混雑で解除が早まりすぎないようにする）。
    until = current + seconds + remaining_seconds(project_root, current)
    payload = {
        "until": until,
        "started_at": current,
        "reason": reason[:300],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear(project_root: Path) -> None:
    """手動再開用にクールダウンを解除する。"""
    path = cooldown_path(project_root)
    if path.exists():
        path.unlink()
