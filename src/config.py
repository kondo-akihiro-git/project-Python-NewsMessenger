"""設定ファイル（YAML）と環境変数（.env）の読み込み.

認証情報は .env からのみ読み込み、設定ファイルやログには保存しない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class FeedConfig:
    """RSS配信元の設定."""

    name: str
    url: str
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    """アプリ全体の設定."""

    project_root: Path
    feeds: tuple[FeedConfig, ...]
    polling_interval_seconds: int
    max_articles_per_run: int
    concurrency: int
    http_timeout_seconds: float
    http_max_retries: int
    http_retry_delays_seconds: tuple[float, ...]
    user_agent: str
    content_min_chars_guideline: int
    max_summary_lines: int
    max_summary_chars: int
    summarize_shortcut: str
    review_shortcut: str
    ai_timeout_seconds: float
    ai_cooldown_seconds: float
    max_revision_attempts: int
    telegram_disable_web_page_preview: bool
    telegram_retry_delays_seconds: tuple[float, ...]
    database_path: Path
    log_file: Path
    bot_token: str = ""
    chat_id: str = ""


def _as_float_tuple(value: Any, default: tuple[float, ...]) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return default
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return default


def load_config(
    project_root: Path | str,
    *,
    config_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> Config:
    """config.yaml（無ければ example）と .env を読み込んで Config を返す."""
    root = Path(project_root).resolve()
    yaml_path = Path(config_path) if config_path else root / "config" / "config.yaml"
    if not yaml_path.exists():
        yaml_path = root / "config" / "config.example.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    dotenv_path = Path(env_path) if env_path else root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)

    polling = data.get("polling") or {}
    http = data.get("http") or {}
    content = data.get("content") or {}
    ai = data.get("ai") or {}
    telegram = data.get("telegram") or {}
    paths = data.get("paths") or {}

    feeds = tuple(
        FeedConfig(
            name=str(item.get("name") or "unnamed"),
            url=str(item.get("url") or ""),
            enabled=bool(item.get("enabled", True)),
        )
        for item in (data.get("feeds") or [])
        if isinstance(item, dict) and str(item.get("url") or "")
    )

    def _resolve_path(value: str, default: str) -> Path:
        candidate = Path(str(value or default))
        return candidate if candidate.is_absolute() else root / candidate

    return Config(
        project_root=root,
        feeds=feeds,
        polling_interval_seconds=int(polling.get("interval_seconds", 300)),
        max_articles_per_run=int(polling.get("max_articles_per_run", 5)),
        concurrency=max(1, int(polling.get("concurrency", 1))),
        http_timeout_seconds=float(http.get("timeout_seconds", 15)),
        http_max_retries=max(1, int(http.get("max_retries", 3))),
        http_retry_delays_seconds=_as_float_tuple(
            http.get("retry_delays_seconds"), (1.0, 3.0, 10.0)
        ),
        user_agent=str(http.get("user_agent") or "NewsTelegramPersonalBot/1.0"),
        content_min_chars_guideline=int(content.get("min_chars_guideline", 400)),
        max_summary_lines=int(content.get("max_summary_lines", 2)),
        max_summary_chars=int(content.get("max_summary_chars", 55)),
        summarize_shortcut=str(ai.get("summarize_shortcut") or "News_Summarize"),
        review_shortcut=str(ai.get("review_shortcut") or "News_Review"),
        ai_timeout_seconds=float(ai.get("timeout_seconds", 60)),
        ai_cooldown_seconds=max(0.0, float(ai.get("cooldown_seconds", 900))),
        max_revision_attempts=max(0, int(ai.get("max_revision_attempts", 2))),
        telegram_disable_web_page_preview=bool(
            telegram.get("disable_web_page_preview", True)
        ),
        telegram_retry_delays_seconds=_as_float_tuple(
            telegram.get("retry_delays_seconds"), (1.0, 3.0, 10.0)
        ),
        database_path=_resolve_path(paths.get("database"), "data/news.db"),
        log_file=_resolve_path(paths.get("log_file"), "logs/application.log"),
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    )
