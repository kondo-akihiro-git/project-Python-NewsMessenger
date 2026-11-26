"""TelegramClient およびログマスキングの単体テスト."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from src.logger import SecretMaskingFilter, sanitize_secrets
from src.telegram_client import TelegramClient, TelegramError

FAKE_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"


def test_sanitize_secrets_masks_bot_token() -> None:
    raw = f"POST https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage failed"
    masked = sanitize_secrets(raw, extra_secrets=(FAKE_TOKEN,))
    assert FAKE_TOKEN not in masked
    assert "***REDACTED***" in masked


def test_secret_masking_filter_masks_log_record() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Error calling https://api.telegram.org/bot%s/getMe",
        args=(FAKE_TOKEN,),
        exc_info=None,
    )
    flt = SecretMaskingFilter(extra_secrets=(FAKE_TOKEN,))
    assert flt.filter(record) is True
    assert FAKE_TOKEN not in record.msg
    assert "***REDACTED***" in record.msg


def test_get_me_and_find_private_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "test_news_bot"}})
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 1,
                            "message": {
                                "chat": {"id": -100999, "type": "group"},
                                "text": "ignore group",
                            },
                        },
                        {
                            "update_id": 2,
                            "message": {
                                "chat": {"id": 987654321, "type": "private", "username": "aki", "first_name": "Aki"},
                                "text": "hello",
                            },
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})

    client = TelegramClient(
        bot_token=FAKE_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    me = client.get_me()
    assert me["username"] == "test_news_bot"

    chat = client.find_latest_private_chat()
    assert chat is not None
    assert chat["chat_id"] == "987654321"
    assert chat["text"] == "hello"


def test_send_message_retries_on_500_then_succeeds() -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(500, json={"ok": False, "description": "Internal Server Error"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client = TelegramClient(
        bot_token=FAKE_TOKEN,
        default_chat_id="987654321",
        retry_delays=(1.0, 3.0, 10.0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=sleeps.append,
    )
    result = client.send_message("テスト要約です。")
    assert result.ok is True
    assert result.message_id == 42
    assert attempts["count"] == 3
    assert sleeps == [1.0, 3.0]


def test_send_message_sends_summary_text_without_link_entities() -> None:
    """本番の通知は要約本文のみで、リンク entity を付けない。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramClient(
        bot_token=FAKE_TOKEN,
        default_chat_id="987654321",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.send_message("会社が発表しました。", disable_web_page_preview=True)
    assert result.ok is True
    assert captured["text"] == "会社が発表しました。"
    assert "entities" not in captured


def test_send_message_does_not_retry_on_401_and_hides_token() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, json={"ok": False, "description": f"Unauthorized for {FAKE_TOKEN}"})

    client = TelegramClient(
        bot_token=FAKE_TOKEN,
        default_chat_id="987654321",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TelegramError) as exc_info:
        client.send_message("テスト")
    assert attempts["count"] == 1
    assert FAKE_TOKEN not in str(exc_info.value)
