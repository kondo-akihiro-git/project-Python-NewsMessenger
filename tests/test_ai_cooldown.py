"""Apple Intelligence クールダウン管理のテスト."""

from __future__ import annotations

import time
from pathlib import Path

from src.ai_cooldown import clear, remaining_seconds, start


def test_no_cooldown_by_default(tmp_path: Path) -> None:
    assert remaining_seconds(tmp_path) == 0.0


def test_start_and_expire(tmp_path: Path) -> None:
    now = time.time()
    start(tmp_path, 900, "混雑")
    assert 890 <= remaining_seconds(tmp_path, now) <= 901
    assert remaining_seconds(tmp_path, now + 1000) == 0.0


def test_start_extends_existing_cooldown(tmp_path: Path) -> None:
    now = time.time()
    start(tmp_path, 900, "最初")
    start(tmp_path, 300, "再検出")
    assert 1190 <= remaining_seconds(tmp_path, now) <= 1201


def test_clear_allows_immediate_retry(tmp_path: Path) -> None:
    start(tmp_path, 900, "混雑")
    clear(tmp_path)
    assert remaining_seconds(tmp_path) == 0.0


def test_broken_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "data" / "ai_cooldown.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    assert remaining_seconds(tmp_path) == 60.0
