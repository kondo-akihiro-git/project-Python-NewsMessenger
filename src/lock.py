"""実行排他用のファイルロック（5分間隔の起動重複を防ぐ）."""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from types import TracebackType
from typing import Type


class RunAlreadyActiveError(Exception):
    """別の実行が既にロックを保持している場合のエラー."""


class RunLock:
    """fcntl.flock によるプロセス間排他ロック."""

    def __init__(
        self,
        path: Path | str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.path = Path(path).resolve()
        self.logger = logger or logging.getLogger("news_telegram.lock")
        self._fp = None

    def acquire(self) -> bool:
        """ロックを取得できたら True、既に保持されていれば False."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fp.close()
            self._fp = None
            return False
        self._fp.seek(0)
        self._fp.truncate()
        self._fp.write(str(os.getpid()))
        self._fp.flush()
        return True

    def release(self) -> None:
        if self._fp is None:
            return
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        finally:
            self._fp.close()
            self._fp = None

    def __enter__(self) -> "RunLock":
        if not self.acquire():
            self.logger.info("既に別の実行が進行中のためスキップします。")
            raise RunAlreadyActiveError("既に別の実行が進行中です。")
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
