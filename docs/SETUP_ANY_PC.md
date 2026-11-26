# SETUP_ANY_PC.md — 別のMac／AIエージェントでの再開手順

このリポジトリをクローンすれば、どのMacでも（Apple Intelligence 対応環境であれば）開発・運用を再開できます。
重要なのは **認証情報と実行済みデータはリポジトリに含まれない** ことです。

## 0. 最初に読むドキュメント（設計書 17-4）

1. `README.md`
2. `DESIGN.md`
3. `AGENTS.md`
4. `CHANGELOG.md`
5. `PROGRESS.md`
6. `docs/MASTER_DESIGN.md`（ユーザー提供の設計書原本）
7. `docs/DECISIONS.md`（判断・認識の記録）
8. 実コードとテスト結果（ドキュメントを無条件に信用しない）

---

## 1. クローン

```bash
git clone https://github.com/kondo-akihiro-git/project-Python-NewsMessenger.git
cd project-Python-NewsMessenger
```

## 2. Python 環境

Python 3.10 系を使用（開発実績: 3.10.12）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install beautifulsoup4 lxml feedparser httpx requests PyYAML pytest pydantic tenacity python-dotenv
```

> このMacでは `/Users/aki/.pyenv/versions/3.10.12/bin/python3` に固定済み。`launchd` の plist は絶対パスで指定しているため、別Macではインストール前に `launchd/com.aki.news-telegram.plist` のパスを書き換えること。

## 3. 認証情報（リポジトリには絶対に置かない）

```bash
cp .env.example .env
chmod 600 .env
# .env を編集して TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID を設定
```

`config/config.yaml` は任意（未作成なら `config/config.example.yaml` にフォールバック）。認証情報を YAML に書かないこと。

## 4. Apple Intelligence ショートカット

`shortcuts/` に署名済み `.shortcut` を保管している。ショートカットアプリへ追加し、以下を確認する。

```bash
shortcuts list        # News_Summarize / News_Review が表示されること
```

ショートカットは「入力テキストをそのまま Apple Intelligence『モデルを使用』へ渡し、出力を返す」汎用構成です。
**プロンプト本文は `config/prompts/` が唯一の管理元**なので、プロンプトを変更してもショートカットの再生成は不要です。

再生成が必要な場合:

```bash
/Users/aki/.pyenv/versions/3.10.12/bin/python3 scripts/build_shortcuts.py
```

## 5. 動作確認

```bash
PY=/Users/aki/.pyenv/versions/3.10.12/bin/python3   # 環境に合わせて変更
$PY -m pytest -q tests/          # 全テスト（2026-11-26時点で66件）
$PY main.py --check-only         # 設定・ショートカット・DB初期化の確認
$PY scripts/test_ai_shortcuts.py --repeat 1   # Apple Intelligence 実機検証（数分）
$PY main.py --limit 1 --no-notify  # RSS→要約→レビューまで（通知なし）
```

## 6. launchd 自動実行（macOS のみ）

```bash
# plist 内の Python パス／プロジェクトパスを自環境へ修正してから
./scripts/install_launchd.sh install
./scripts/install_launchd.sh status
```

- GUI ユーザセッション前提（`~/Library/LaunchAgents/`）。
- 5分間隔。`src/lock.py` のファイルロックで重複起動をスキップ。
- 停止したい場合: `./scripts/install_launchd.sh uninstall`

## 7. 再開時の注意（必須）

- **体言止めは1行でも禁止**。要約・レビュー・修正の各プロンプトと `src/text_quality.py` の機械ガードを維持すること。
- 品質を保証できない場合は通知しない（フェイルクローズ）。有料APIへのフォールバックは絶対に追加しない。
- 既存の DB（`data/news.db`）・ログは Git 管理外。通知履歴を引き継ぎたい場合は別途安全にコピーする。
- 実RSS・実通知の検証はネットワークと Apple Intelligence が必要。テストだけならオフラインで完結する。
