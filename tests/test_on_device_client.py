"""オンデバイス Apple Intelligence クライアントのテスト."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.ai_client import (
    AIClientError,
    AIRateLimitError,
    AITimeoutError,
    InvalidAIResponseError,
    OnDeviceAIClient,
)


class _FakeRunner:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc
        return self.result


def _result(stdout="OK", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_run_shortcut_uses_arm64_bridge_and_stdin():
    runner = _FakeRunner(_result(stdout='{"status":"OK"}'))
    client = OnDeviceAIClient(runner=runner)
    out = client.run_shortcut("unused", "プロンプト本文")

    assert out == '{"status":"OK"}'
    cmd, kwargs = runner.calls[0]
    assert cmd[:3] == ["/usr/bin/arch", "-arm64", "/usr/bin/python3"]
    assert cmd[3].endswith("apple_llm_bridge.py")
    assert cmd[4] == "--schema"
    assert '"status"' in cmd[5]
    assert kwargs["input"] == "プロンプト本文"


def test_review_uses_review_schema():
    runner = _FakeRunner(_result(stdout='{"status":"PASS"}'))
    client = OnDeviceAIClient(runner=runner)
    client.run_shortcut("News_Review", "プロンプト")
    cmd = runner.calls[0][0]
    assert '"unsupported_claims"' in cmd[5]


def test_run_shortcut_raises_rate_limit_on_busy_message():
    runner = _FakeRunner(
        _result(
            stdout="",
            stderr="Error: 現在、リクエストを完了できません。",
            returncode=1,
        )
    )
    client = OnDeviceAIClient(runner=runner)
    with pytest.raises(AIRateLimitError):
        client.run_shortcut("unused", "プロンプト")


def test_run_shortcut_raises_error_when_output_empty():
    runner = _FakeRunner(_result(stdout="   ", stderr="", returncode=0))
    client = OnDeviceAIClient(runner=runner)
    with pytest.raises(InvalidAIResponseError):
        client.run_shortcut("unused", "プロンプト")


def test_run_shortcut_raises_client_error_on_failure():
    runner = _FakeRunner(_result(stdout="", stderr="ModelUnavailableError: not ready", returncode=3))
    client = OnDeviceAIClient(runner=runner)
    with pytest.raises(AIClientError):
        client.run_shortcut("unused", "プロンプト")


def test_run_shortcut_raises_timeout():
    runner = _FakeRunner(exc=subprocess.TimeoutExpired(cmd=["x"], timeout=1))
    client = OnDeviceAIClient(runner=runner, timeout_seconds=1)
    with pytest.raises(AITimeoutError):
        client.run_shortcut("unused", "プロンプト")
