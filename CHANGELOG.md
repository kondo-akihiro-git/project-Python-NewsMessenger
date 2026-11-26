# CHANGELOG.md

## [Unreleased] - 2026-11-25

### Added
- Phase 1（環境調査）を実施し、macOS / Python / `shortcuts` CLI / Telegram 環境を確認。
- プロジェクト初期ディレクトリ `/Users/aki/.gemini/antigravity/scratch/news-telegram` を作成。
- 引き継ぎ用ドキュメント（`README.md`, `DESIGN.md`, `AGENTS.md`, `CHANGELOG.md`, `PROGRESS.md`）を作成。
- Phase 2（Telegram単体テスト）向け基盤を実装し、iPhone実機通知テストを完了。
- Phase 3（Apple Intelligence × ショートカット連携）向け実装を追加:
  - `config/prompts/summarize.txt`, `review.txt`, `revise.txt`（3行以内・URL非混入・プロンプトインジェクション対策入り）
  - `src/ai_client.py`（`shortcuts run` ラッパー、JSON抽出・修復、構造・文字数・行数・URL混入バリデーション、エラー種別判定）
  - `scripts/build_shortcuts.py`（`.shortcut` 生成・`shortcuts sign` 署名自動化）
  - `shortcuts/News_Summarize.shortcut`, `shortcuts/News_Review.shortcut`（署名済み、ショートカットアプリへ登録済み）
  - `scripts/test_ai_shortcuts.py`（要約・レビュー・誤要約検出・連続実行の実機検証スクリプト）
- Phase 4（RSS取得・本文抽出・SQLite保存）を追加:
  - `src/config.py`（YAML + `.env` の設定集約、認証情報はファイルに保存しない）
  - `src/models.py`（`FeedItem` / `Article` / 状態定数 / URL正規化と記事キー生成）
  - `src/rss_client.py`（`httpx` + `feedparser`、リトライ・フィード単位の失敗分離）
  - `src/content_extractor.py`（`httpx` + BeautifulSoup/lxml、ノイズ除去・本文選択・十分性判定）
  - `src/database.py`（SQLite WAL、冪等な記事登録、状態遷移とイベントログ）
  - `tests/test_rss_client.py`（4件）, `tests/test_content_extractor.py`（4件）, `tests/test_database.py`（4件）
  - `tests/test_ai_client.py` に生改行JSON修復・完全JSON待機のテストを追加（7件→9件）
- Phase 5（要約・レビュー・修正ループ）を追加:
  - `src/ai_client.py` に `revise()` を追加（修正プロンプト汎用ショートカット実行）
  - `src/models.py` に `REVIEW_FAILED` 状態を追加
  - `src/pipeline.py`（RSS検出 → 本文取得 → 要約 → レビュー → 修正→再レビュー → 承認/失敗状態、1実行のオーケストレーション）
  - `tests/test_pipeline.py`（7件: 承認・修正ループ・不合格打ち切り・本文不足・要約失敗・情報不足・run_once）
- Phase 6（Telegram統合・冪等性）を追加:
  - `src/notifier.py`（要約本文＋元記事リンクの通知テキスト生成、URL混入/空要約/行数超過のフェイルクローズ、送信成功時のみ NOTIFIED、`notified_at` による二重送信防止、最大3回リトライ）
  - `main.py`（`--limit` / `--no-notify` / `--check-only` エントリポイント）
  - `tests/test_notifier.py`（6件）
- Phase 7（launchd・排他制御）を追加:
  - `src/lock.py`（`fcntl.flock` による実行排他、重複起動スキップ）
  - `launchd/com.aki.news-telegram.plist`（5分間隔、GUIユーザセッション、絶対パス）
  - `scripts/install_launchd.sh`（install / uninstall / status）
  - `tests/test_lock.py`（2件）
- Phase 8（総合テスト）を実施:
  - 実RSS 108件取得 → 実記事1件を要約・レビュー承認 → 実Telegram送信成功（message_id=21）
  - `main.py --check-only` 成功
- launchd エージェントをインストール・有効化（`gui/501/com.aki.news-telegram`、5分間隔）。

### 追加改善（2026-11-26）
- `config/prompts/{summarize,review,revise}.txt` を改訂: 主体（固有名詞）・出来事名・対象・理由（〜を受けて）・対応（数値目標）・目的の6要素を必須化し、要約を35〜55文字に明示。レビュー不合格基準に重要キーワード欠落を追加。
- `config/config.example.yaml`: `max_summary_chars` を 90→55 に変更。
- `src/notifier.py`: 55文字超の要約を送信前に拒否。URL を本文に書かず `text_link` entity で「元記事を読む」をリンク化し、通知の表示文字数を最大55文字に短縮。
- `src/telegram_client.py`: `send_message()` に `entities` 引数を追加。
- `tests/test_notifier.py` / `tests/test_telegram.py` に文字数チェック・entity 渡しのテストを追加（全51件PASS）。
- 検証: Microsoft 記事で `米マイクロソフトがコードレッドを宣言 / 世界的な大規模障害を受けて / 復旧を1時間以内に終える目標`（45文字）を生成し、短縮通知を実送信（message_id=22）。※この要約の3行目は体言止めだったため、下記で全面禁止に改訂。

### 体言止め禁止・ドキュメント整備・Git公開（2026-11-26）
- ユーザーからの再指示を受け、体言止めを「連続禁止」から **1行でも禁止** に強化。
  - `config/prompts/{summarize,review,revise}.txt` に明示（述語で終える、名詞止めはレビュー不合格）。
  - `src/text_quality.py`（新規）: `has_taigen_dome()` で代表的な名詞語尾を検出。
  - `src/ai_client.py`: 要約検証時に体言止めを拒否し再生成対象に。
  - `src/notifier.py`: 送信前の最終検査でも体言止めを拒否。
  - `tests/test_ai_client.py` / `tests/test_notifier.py` にテスト追加（全53件PASS）。
- ドキュメントを追加・更新:
  - `docs/MASTER_DESIGN.md`（ユーザー提供の詳細設計書原本）
  - `docs/DECISIONS.md`（設計判断・ユーザー指示の記録）
  - `docs/SETUP_ANY_PC.md`（別Mac／AIエージェント向け再開手順）
  - `AGENTS.md`, `README.md`, `DESIGN.md`, `PROGRESS.md` を最新状態へ更新。
- Git リポジトリを初期化し、公開リポジトリ `project-Python-NewsMessenger` へコミット・プッシュ。認証情報・DB・ログはコミットしない。

### Changed
- Phase 2 実機通知テストの結果を反映し、要約目標を「3行以内・合計45〜55文字目安」、要約本文へのリンク混入禁止（末尾に分離配置）へ仕様確定。
- AI呼び出しが1〜3分かかる実測を反映し、`max_articles_per_run` を 5→1 に変更。
- `PROGRESS.md` を最終状態（Phase 1〜8 完了・49テストPASS・実通知成功）へ更新。
- `PROGRESS.md` を実測値（Phase 3 実機検証結果・Phase 4 スモークテスト結果・テスト33件PASS）に合わせて全面更新。
- Python 実行パスを `/Users/aki/.pyenv/versions/3.10.12/bin/python3` に固定。

### Fixed
- Apple Intelligence が JSON 文字列内に生の改行を出力し `json.loads` が失敗する問題を修正（`repair_json_string_control_chars()`）。
- `shortcuts run` 終了後も出力ファイルへ非同期書き込みが続き、途中の JSON を採用してしまう問題を修正（`require_json_object=True` で完全なオブジェクトまで待機、`stable_for=2.0` / `max_wait=90.0` に延長）。
- `src/rss_client.py` の警告ログ書式（プレースホルダと引数の不一致）を修正。
- `src/content_extractor.py` のテスト期待値を、重複段落除去・文字数目安の実装に合わせて修正。
- 実E2Eで Yahoo!ニュースの302リダイレクトにより本文取得が失敗していた問題を修正（`httpx` に `follow_redirects=True` を設定、リダイレクト追跡テスト追加）。
