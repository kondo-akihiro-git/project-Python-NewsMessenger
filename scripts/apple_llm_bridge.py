#!/usr/bin/env python3
"""Apple Intelligence のオンデバイスモデルを呼び出すブリッジ.

このスクリプトは Apple Silicon のネイティブ Python (arm64) で実行する。
プロンプトを標準入力から受け取り、モデルの生出力を標準出力へ書く。
--schema を渡した場合はガイド付き生成（構造化出力）を使い、JSON を返す。
AWS や OpenAI などの外部 API は一切使用しない。
"""

from __future__ import annotations

import argparse
import json
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", help="JSON Schema 文字列（ガイド付き生成）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("プロンプトが空です。", file=sys.stderr)
        return 2

    schema = None
    if args.schema:
        try:
            schema = json.loads(args.schema)
        except json.JSONDecodeError as exc:
            print(f"スキーマが不正です: {exc}", file=sys.stderr)
            return 5

    try:
        from apple_llm import AppleLLM
    except ImportError as exc:
        print(f"apple-llm が利用できません: {exc}", file=sys.stderr)
        return 3

    try:
        with AppleLLM(tier="device") as llm:
            if schema is not None:
                output = llm.json(prompt, schema=schema)
                if not isinstance(output, dict):
                    output = json.loads(str(output))
                # ガイド付き生成でもまれにキーが欠ける。事実は足さず、型だけを補完する。
                for key, spec in schema.get("properties", {}).items():
                    if key == "status" or key in output:
                        continue
                    if spec.get("type") == "array":
                        output[key] = []
                    elif spec.get("type") == "boolean":
                        # 品質確認の真偽値が欠けた場合は不合格扱いにしてフェイルクローズする。
                        output[key] = False
                    else:
                        output[key] = ""
                output = json.dumps(output, ensure_ascii=False)
            else:
                output = llm.text(prompt)
    except Exception as exc:  # noqa: BLE001 - ブリッジは例外種別を親へ渡すため文字列化する
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not output or not output.strip():
        print("モデル出力が空です。", file=sys.stderr)
        return 4

    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
