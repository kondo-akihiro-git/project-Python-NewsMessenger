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

## 4. Apple Intelligence（オンデバイス呼び出し）

2026-11-26 以降、既定の AI 呼び出しは **オンデバイス Apple Intelligence** です。Cloud モデルが長時間応答しないため、Shortcuts 経由の Cloud 呼び出しは既定では使用しません。有料APIへのフォールバックは禁止です。

必要なもの:

- Apple Silicon Mac（Foundation Models 対応）＋ macOS 言語設定が日本語
- **Command Line Tools for Xcode 27.2 以降**（`swiftc --version` で確認）
- `apple-llm` を **arm64 の `/usr/bin/python3`** から import できること

```bash
xcode-select --install   # またはソフトウェアアップデートで CLT 27.2 を導入
/usr/bin/arch -arm64 /usr/bin/python3 -c "import apple_llm; print('apple_llm OK')"
```

呼び出しは `scripts/apple_llm_bridge.py` が `arch -arm64` で起動します。`--schema` 指定時は `llm.json()` のガイド付き生成でJSONの形を固定します。

旧ショートカット（`shortcuts/News_Summarize.shortcut` / `News_Review.shortcut`）は登録済みのままですが、既定では使用しません。**プロンプト本文は `config/prompts/` が唯一の管理元**です。

## 5. 動作確認

```bash
PY=/Users/aki/.pyenv/versions/3.10.12/bin/python3   # 環境に合わせて変更
$PY -m pytest -q tests/          # 全テスト（2026-11-26時点で79件）
$PY main.py --check-only         # 設定・オンデバイスAI・DB初期化の確認
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
- **通知は要約文章のみ**。「元記事を読む」やURLのリンク構成は user 指示で廃止済み。再追加しないこと。
- **初見キーワードには説明を必ず添える**（例: `dotsという常時稼働型エージェント`、`5G SAという5G専用設備`）。説明は記事本文のみを根拠とし、55文字以内で6要素・述語を維持する。
- **要約は55文字以内・最大2行**。1行目に主要ニュース、2行目は補足のみ。記事に複数のイベントがあっても主要イベント1つに絞る。
- **4W1Hと具体性**: WHO（氏名入りの主体）とWHAT（何が起きたか。何の原因／何への対応か）は必須。「原因究明を求めました」だけ、役職だけ（「総務相」）、根拠のない「信頼が揺らぐ」は不合格。
- 品質を保証できない場合は通知しない（フェイルクローズ）。有料APIへのフォールバックは絶対に追加しない。
- 既存の DB（`data/news.db`）・ログは Git 管理外。通知履歴を引き継ぎたい場合は別途安全にコピーする。
- 実RSS・実通知の検証はネットワークと Apple Intelligence が必要。テストだけならオフラインで完結する。
