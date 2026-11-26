"""Telegram Bot API クライアント."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from src.logger import sanitize_secrets

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_RETRY_DELAYS = (1.0, 3.0, 10.0)


class TelegramError(Exception):
    """Telegram API 通信における基底エラー."""


@dataclass(frozen=True)
class TelegramSendResult:
    """Telegram sendMessage の送信結果."""

    ok: bool
    message_id: int | None
    chat_id: str
    error: str | None = None


class TelegramClient:
    """Telegram Bot API との通信を担当するクライアント."""

    def __init__(
        self,
        bot_token: str,
        default_chat_id: str | None = None,
        timeout_seconds: float = 15.0,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        http_client: httpx.Client | None = None,
        logger: logging.Logger | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        token = bot_token.strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN が空です。")
        self._bot_token = token
        self._default_chat_id = default_chat_id.strip() if default_chat_id else None
        self._timeout = timeout_seconds
        self._retry_delays = retry_delays
        self._external_client = http_client
        self._logger = logger or logging.getLogger("news_telegram.telegram")
        self._sleep = sleep_fn

    def _endpoint(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self._bot_token}/{method}"

    def _sanitize(self, message: str) -> str:
        return sanitize_secrets(message, (self._bot_token,))

    def _request(
        self,
        http_method: str,
        api_method: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._endpoint(api_method)
        max_attempts = len(self._retry_delays) + 1
        last_error: str = "不明なエラー"

        for attempt in range(1, max_attempts + 1):
            try:
                if self._external_client is not None:
                    resp = self._external_client.request(
                        http_method,
                        url,
                        params=params,
                        json=json_body,
                        timeout=self._timeout,
                    )
                else:
                    with httpx.Client(timeout=self._timeout) as client:
                        resp = client.request(
                            http_method,
                            url,
                            params=params,
                            json=json_body,
                        )

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok") is True:
                        return data
                    desc = self._sanitize(str(data.get("description", "API returned ok=false")))
                    raise TelegramError(f"Telegram APIエラー: {desc}")

                # 4xx（429以外）は再試行しても回復しないため即時失敗
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    detail = self._extract_error_detail(resp)
                    raise TelegramError(
                        f"Telegram APIクライアントエラー (HTTP {resp.status_code}): {detail}"
                    )

                last_error = f"HTTP {resp.status_code}: {self._extract_error_detail(resp)}"
                delay = self._compute_retry_delay(resp, attempt)

            except TelegramError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
                last_error = self._sanitize(f"{type(exc).__name__}: {exc}")
                delay = (
                    self._retry_delays[attempt - 1]
                    if attempt - 1 < len(self._retry_delays)
                    else 0.0
                )

            if attempt < max_attempts:
                self._logger.warning(
                    "Telegram API %s 失敗 (試行 %d/%d): %s -> %.1f秒後に再試行",
                    api_method,
                    attempt,
                    max_attempts,
                    last_error,
                    delay,
                )
                if delay > 0:
                    self._sleep(delay)

        raise TelegramError(
            f"Telegram API {api_method} が最大試行回数 ({max_attempts}) 後に失敗しました: {last_error}"
        )

    def _extract_error_detail(self, resp: httpx.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict) and "description" in body:
                return self._sanitize(str(body["description"]))
        except Exception:
            pass
        return self._sanitize(resp.text[:200])

    def _compute_retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        if resp.status_code == 429:
            try:
                body = resp.json()
                retry_after = body.get("parameters", {}).get("retry_after")
                if isinstance(retry_after, (int, float)) and retry_after > 0:
                    return float(min(retry_after, 60))
            except Exception:
                pass
        if attempt - 1 < len(self._retry_delays):
            return self._retry_delays[attempt - 1]
        return 0.0

    def get_me(self) -> dict[str, Any]:
        """Bot自身の情報を取得し、トークンの有効性を確認する."""
        data = self._request("GET", "getMe")
        return data["result"]

    def get_updates(self, limit: int = 20) -> list[dict[str, Any]]:
        """受信した最新メッセージ一覧を取得する."""
        data = self._request("GET", "getUpdates", params={"limit": limit})
        result = data.get("result", [])
        return result if isinstance(result, list) else []

    def find_latest_private_chat(self) -> dict[str, str] | None:
        """getUpdates から最新のプライベートチャット情報 (chat_id, username) を返す."""
        updates = self.get_updates(limit=50)
        for update in reversed(updates):
            msg = update.get("message") or update.get("edited_message")
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat")
            if not isinstance(chat, dict):
                continue
            if chat.get("type") == "private" and "id" in chat:
                return {
                    "chat_id": str(chat["id"]),
                    "username": str(chat.get("username") or ""),
                    "first_name": str(chat.get("first_name") or ""),
                    "text": str(msg.get("text") or ""),
                }
        return None

    def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        disable_web_page_preview: bool = True,
        entities: Sequence[dict[str, Any]] | None = None,
    ) -> TelegramSendResult:
        """テキストメッセージを送信する."""
        target_chat_id = (chat_id or self._default_chat_id or "").strip()
        if not target_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID が指定されていません。")
        if not text or not text.strip():
            raise ValueError("送信メッセージが空です。")

        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if entities:
            payload["entities"] = [dict(entity) for entity in entities]
        data = self._request("POST", "sendMessage", json_body=payload)
        result = data.get("result", {})
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return TelegramSendResult(
            ok=True,
            message_id=message_id,
            chat_id=target_chat_id,
        )
