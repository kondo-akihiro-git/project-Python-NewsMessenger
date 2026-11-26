# news-telegram

Yahoo!ニュースおよびITmedia NEWSなどのRSSフィードから新着ニュースを取得し、記事本文を検証したうえで **オンデバイス Apple Intelligence**（Apple Silicon の Foundation Models を `apple-llm` 経由で直接呼び出し）で要約・レビューし、品質基準を満たした記事のみを Telegram Bot 経由で iPhone に通知するシステムです。

> 2026-11-26 から macOS ショートカットの Cloud モデルが長時間「現在、リクエストを完了できません。」を返し続けたため、既定の AI 呼び出しをオンデバイスモデルへ変更しました。有料AI APIへのフォールバックは行いません。

## 現在の開発状況

- **完了**: Phase 1〜8（すべて実装・検証済み）
- **テスト**: `pytest -q tests/` → **79件PASS**
- **実統合検証**: 実RSS 108件取得 → 実記事1件をレビュー承認 → 実Telegram送信成功（オンデバイスAI版でも再確認済み）
- **launchd**: インストール・有効化済み（5分間隔、GUIユーザセッション）
- **通知形式**: **要約文章のみ**を送信します。ユーザー指示により「元記事を読む」などのリンク文言・URL・`text_link` entity は一切付けません。
- **日本語品質**: 体言止めは1行でも禁止（プロンプト＋`src/text_quality.py` の機械ガード）。不自然な述語（「〜としました」など）も拒否します。
- **初見キーワード**: 小学生が初見の用語（例: dots、APIルーター、コードレッド）には、記事本文だけを根拠にした短い説明を要約内に必ず添えます。説明がない場合はレビューで不合格とし、修正またはフェイルクローズします。
- **機械ガード**: 55文字以内・3行以内に加え、最低35文字・見出しの固有語2つ以上（主体と出来事名の手掛かり）を必須とする。満たさない要約はレビューを待たずにフェイルクローズ。
- **Git**: 公開リポジトリ `project-Python-NewsMessenger` で管理。
- 詳細は [PROGRESS.md](PROGRESS.md)、設計書原本は [docs/MASTER_DESIGN.md](docs/MASTER_DESIGN.md)、判断履歴は [docs/DECISIONS.md](docs/DECISIONS.md)、別Macでの再開手順は [docs/SETUP_ANY_PC.md](docs/SETUP_ANY_PC.md) を参照してください。

## 開発・実行コマンド

```bash
cd /Users/aki/.gemini/antigravity/scratch/news-telegram
PY=/Users/aki/.pyenv/versions/3.10.12/bin/python3

# 単体テスト
$PY -m pytest -q tests/

# 設定・オンデバイスApple Intelligence・DB初期化の確認
$PY main.py --check-only

# 1回の実行（RSS取得 → 要約 → レビュー → 通知）
$PY main.py --limit 1

# 通知せずにAI処理だけ確認
$PY main.py --limit 1 --no-notify

# Apple Intelligence ショートカットの実機検証
$PY scripts/test_ai_shortcuts.py --repeat 1

# launchd 自動実行の状態確認 / 無効化 / 再インストール
./scripts/install_launchd.sh status
./scripts/install_launchd.sh uninstall
./scripts/install_launchd.sh install
```

launchd はすでにインストール・有効化済みです（`com.aki.news-telegram`、5分間隔）。

- オンデバイス Apple Intelligence の利用には **Command Line Tools for Xcode 27.2 以降** と **arm64 の `/usr/bin/python3` に `apple-llm`** が必要です（本Macでは導入済み）。`scripts/apple_llm_bridge.py` が `arch -arm64` で起動するブリッジです。
- 旧ショートカット（`News_Summarize`, `News_Review`）は登録済みのままですが、既定では使用しません。
- 5分間隔の自動実行でも `src/lock.py` のファイルロックにより処理が重なりません。
- AI呼び出しに1〜3分かかるため、`max_articles_per_run` は **1件** を推奨します。
- 品質を保証できない場合（本文不足・要約不良・レビュー不合格・送信失敗）は通知しません。

## 推奨構成（設計ベース）

- **実行環境**: macOS (`launchd` 定期実行 + Python 3.10)
- **AI処理**: `apple-llm` の `tier="device"` + Foundation Models（ガイド付きJSON生成）
- **データ管理**: SQLite (`data/news.db`)
- **通知**: Telegram Bot API (`sendMessage`)

## 注意事項

- 有料AI APIへの自動切り替えや追加課金は行いません。
- Telegram Bot Token 等の認証情報はリポジトリやログに保存しません。
- 要約の品質を保証できない場合や記事本文が不足している場合は通知しません。
