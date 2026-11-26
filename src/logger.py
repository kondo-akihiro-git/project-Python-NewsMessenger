"""ロギング設定および秘密情報マスキングフィルタ."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_TELEGRAM_TOKEN_PATTERN = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+", re.IGNORECASE)


def sanitize_secrets(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """ログ出力文字列からBotトークン等の秘密情報をマスクする."""
    masked = text
    for secret in extra_secrets:
        if secret:
            masked = masked.replace(secret, "***REDACTED***")
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if env_token:
        masked = masked.replace(env_token, "***REDACTED***")
    masked = _TELEGRAM_TOKEN_PATTERN.sub("bot***REDACTED***", masked)
    masked = _BEARER_PATTERN.sub(r"\1***REDACTED***", masked)
    return masked


class SecretMaskingFilter(logging.Filter):
    """ログレコードのメッセージおよび引数から秘密情報を除去するフィルタ."""

    def __init__(self, extra_secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._extra_secrets = tuple(s for s in extra_secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatted = record.getMessage()
        except Exception:
            formatted = str(record.msg)
        record.msg = sanitize_secrets(formatted, self._extra_secrets)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # 例外メッセージ内の秘密情報露出を防ぐためテキスト化して記録する
            record.exc_text = sanitize_secrets(
                logging.Formatter().formatException(record.exc_info),
                self._extra_secrets,
            )
            record.exc_info = None
        return True


def setup_logger(
    name: str = "news_telegram",
    log_file: Path | str | None = None,
    level: int = logging.INFO,
    extra_secrets: tuple[str, ...] = (),
) -> logging.Logger:
    """秘密情報マスキング付きのロガーを構築して返す."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mask_filter = SecretMaskingFilter(extra_secrets=extra_secrets)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(mask_filter)
    logger.addHandler(stream_handler)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(mask_filter)
        logger.addHandler(file_handler)

    return logger
