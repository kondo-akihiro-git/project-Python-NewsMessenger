"""RunLock の排他制御テスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lock import RunAlreadyActiveError, RunLock


def test_lock_prevents_overlapping_runs(tmp_path: Path) -> None:
    first = RunLock(tmp_path / "run.lock")
    second = RunLock(tmp_path / "run.lock")
    with first:
        assert second.acquire() is False
        assert (tmp_path / "run.lock").read_text(encoding="utf-8") != ""
        with pytest.raises(RunAlreadyActiveError):
            with second:
                pass

    assert second.acquire() is True
    second.release()


def test_lock_is_released_after_exception(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "run.lock")
    with pytest.raises(RuntimeError, match="boom"):
        with lock:
            raise RuntimeError("boom")
    assert lock.acquire() is True
    lock.release()
