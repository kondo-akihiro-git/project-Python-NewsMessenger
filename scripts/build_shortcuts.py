#!/usr/bin/env python3
"""News_Summarize / News_Review の .shortcut ファイルを生成・署名するスクリプト."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHORTCUTS_DIR = PROJECT_ROOT / "shortcuts"


def build_llm_shortcut_plist(model_name: str = "Apple Intelligence") -> dict:
    """ショートカット入力を受け取り、Apple Intelligence の出力を返すワークフロー定義を返す."""
    askllm_uuid = str(uuid.uuid4()).upper()

    return {
        "WFWorkflowClientVersion": "2700",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 61440,
            "WFWorkflowIconImageData": b"",
            "WFWorkflowIconStartColor": 4282601983,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [
            "WFStringContentItem",
        ],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowOutputContentItemClasses": [
            "WFStringContentItem",
        ],
        "WFWorkflowTypes": [],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.askllm",
                "WFWorkflowActionParameters": {
                    "UUID": askllm_uuid,
                    "WFAllowWebSearch": False,
                    "WFGenerativeResultType": "Text",
                    "WFLLMModel": model_name,
                    "WFLLMPrompt": {
                        "Value": {
                            "attachmentsByRange": {
                                "{0, 1}": {
                                    "Type": "ExtensionInput",
                                }
                            },
                            "string": "\ufffc",
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.output",
                "WFWorkflowActionParameters": {
                    "WFOutput": {
                        "Value": {
                            "attachmentsByRange": {
                                "{0, 1}": {
                                    "OutputName": "モデルを使用",
                                    "OutputUUID": askllm_uuid,
                                    "Type": "ActionOutput",
                                }
                            },
                            "string": "\ufffc",
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                },
            },
        ],
    }


def create_signed_shortcut(name: str, output_dir: Path, model_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{name}.shortcut"
    payload = build_llm_shortcut_plist(model_name=model_name)

    with tempfile.TemporaryDirectory(prefix="build_sc_") as tmp_dir:
        unsigned_path = Path(tmp_dir) / f"{name}.unsigned.shortcut"
        with unsigned_path.open("wb") as fp:
            plistlib.dump(payload, fp, fmt=plistlib.FMT_BINARY)

        proc = subprocess.run(
            [
                "/usr/bin/shortcuts",
                "sign",
                "--mode",
                "anyone",
                "--input",
                str(unsigned_path),
                "--output",
                str(final_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"shortcuts sign 失敗 ({name}): {err}")

    return final_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="News_Summarize / News_Review .shortcut ファイルを生成・署名する"
    )
    parser.add_argument(
        "--model",
        default="Apple Intelligence",
        help="WFLLMModel に設定するモデル名（デフォルト: Apple Intelligence）",
    )
    args = parser.parse_args()

    for name in ("News_Summarize", "News_Review"):
        try:
            path = create_signed_shortcut(name, SHORTCUTS_DIR, args.model)
            print(f"生成・署名完了: {path}")
        except Exception as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

    print("\n次のコマンドでショートカットアプリにインポートできます:")
    print(
        f"  open {SHORTCUTS_DIR / 'News_Summarize.shortcut'} {SHORTCUTS_DIR / 'News_Review.shortcut'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
