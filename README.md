# news-telegram

Yahoo!ニュースおよびITmedia NEWSなどのRSSフィードから新着ニュースを取得し、記事本文を検証したうえで Apple Intelligence（macOSショートカット経由）で小学校3年生程度の分かりやすい日本語に要約・レビューし、品質基準を満たした記事のみを Telegram Bot 経由で iPhone に通知するシステムです。

## 現在の開発状況

- **完了**: Phase 1〜8（すべて実装・検証済み）
- **テスト**: `pytest -q tests/` → **66件PASS**
- **実統合検証**: 実RSS 108件取得 → 実記事1件をレビュー承認 → 実Telegram送信成功
- **launchd**: インストール・有効化済み（5分間隔、GUIユーザセッション）
- **通知形式**: 要約は35〜55文字。URL は本文に出さず「元記事を読む」をリンク化するため、iPhone通知で省略されません。
- **日本語品質**: 体言止めは1行でも禁止（プロンプト＋`src/text_quality.py` の機械ガード）。
- **Git**: 公開リポジトリ `project-Python-NewsMessenger` で管理。
- 詳細は [PROGRESS.md](PROGRESS.md)、設計書原本は [docs/MASTER_DESIGN.md](docs/MASTER_DESIGN.md)、判断履歴は [docs/DECISIONS.md](docs/DECISIONS.md)、別Macでの再開手順は [docs/SETUP_ANY_PC.md](docs/SETUP_ANY_PC.md) を参照してください。

## 開発・実行コマンド

```bash
cd /Users/aki/.gemini/antigravity/scratch/news-telegram
PY=/Users/aki/.pyenv/versions/3.10.12/bin/python3

# 単体テスト
$PY -m pytest -q tests/

# 設定・ショートカット登録・DB初期化の確認
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

- Apple Intelligence のショートカット（`News_Summarize`, `News_Review`）はショートカットアプリに登録済みです。
- 5分間隔の自動実行でも `src/lock.py` のファイルロックにより処理が重なりません。
- AI呼び出しに1〜3分かかるため、`max_articles_per_run` は **1件** を推奨します。
- 品質を保証できない場合（本文不足・要約不良・レビュー不合格・送信失敗）は通知しません。

## 推奨構成（設計ベース）

- **実行環境**: macOS (`launchd` 定期実行 + Python 3.10)
- **AI処理**: macOS ショートカット (`News_Summarize`, `News_Review`) + Apple Intelligence
- **データ管理**: SQLite (`data/news.db`)
- **通知**: Telegram Bot API (`sendMessage`)

## 注意事項

- 有料AI APIへの自動切り替えや追加課金は行いません。
- Telegram Bot Token 等の認証情報はリポジトリやログに保存しません。
- 要約の品質を保証できない場合や記事本文が不足している場合は通知しません。
