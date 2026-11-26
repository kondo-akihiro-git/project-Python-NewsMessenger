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
  - `tests/test_ai_client.py` / `tests/test_notifier.py` にテスト追加（全59件PASS）。
- ドキュメントを追加・更新:
  - `docs/MASTER_DESIGN.md`（ユーザー提供の詳細設計書原本）
  - `docs/DECISIONS.md`（設計判断・ユーザー指示の記録）
  - `docs/SETUP_ANY_PC.md`（別Mac／AIエージェント向け再開手順）
  - `AGENTS.md`, `README.md`, `DESIGN.md`, `PROGRESS.md` を最新状態へ更新。
- Git リポジトリを初期化し、公開リポジトリ `project-Python-NewsMessenger` へコミット・プッシュ。認証情報・DB・ログはコミットしない。
- 実機検証で、新プロンプトの出力が `summary` 直後で切れる問題を確認。要約自体は述語終わりで体言止め解消。
  - `src/ai_client.py`: 既定 `timeout_seconds` 60→240秒、`max_wait` 90→180秒に延長し、不完全JSONなら追加ポーリング。
  - `config/config.example.yaml`: `ai.timeout_seconds: 240` に更新（実測 要約97秒／レビュー193秒）。
- 実機ログで確認した Apple Intelligence の出力揺れに対応:
  - 体言止めルールの例示を簡潔化し、プロンプト全体を短縮。
  - `src/ai_client.py`: 文字列の閉じ引用符が日本語の鉤括弧（`」`）になった JSON を修復する候補を追加（後段の厳密検証は維持）。
  - `tests/test_ai_client.py` に修復テストを追加（全59件PASS）。
- 要約JSONが `summary` 直後で切れる問題への追加対応:
  - `src/ai_client.py`: status/summary 後に出力が終了した場合、事実を補わず欠落メタフィールドを「不明」/空として明示する復旧候補を追加。要約本文はAI実出力を保持し、体言止め・文字数・空文字の検査とレビューAIの事実照合は従来どおり実施（フェイルクローズ維持）。空要約は復旧しない。
  - `config/prompts/{summarize,revise}.txt`: コード側で使われていない `event` / `target` / `action` / `purpose` フィールドを出力形式から削除し、JSONを短縮して切り上げリスクを低減。6要素は要約本文に含めるルールは維持。
  - `tests/test_ai_client.py` に切り上げ復旧テスト（成功・空要約拒否）を追加（全59件PASS）。
- 要約が文字数・行数を超えた場合の再試行を改善:
  - `src/ai_client.py`: 次回試行のプロンプトにのみ「上限以内に短くしてください」という再生成指示を付与。事実の追加は指示せず、述語で終えるルールとレビューによる事実照合は維持。
  - `tests/test_ai_client.py` に再試行時プロンプトの検証テストを追加（全59件PASS）。
- `location` / `reason` が文字列ではなく文字列配列で返されるケースに対応:
  - `src/ai_client.py::_ensure_str()` で配列を連結して正規化（事実の追加はしない）。
  - `tests/test_ai_client.py` に正規化テストを追加（全59件PASS）。
- ユーザー明示: 「55文字も6要素もどちらも絶対に守る」。文字数上限の緩和・6要素の削減は行わない方針を記録。
  - `config/prompts/summarize.txt` / `revise.txt`: 文字数ルールを最優先に配置し、行ごとの文字数目安と「事実を削らず言い換える」具体例を追加。6要素は「削らず短い言い換えで残す」と明記。
  - `src/ai_client.py`: 文字数超過時の再試行で、直前の要約本文と実際の文字数を渡し、6要素と述語を残した言い換えを指示するよう改善。
  - 体言止め検出時も再試行対象とし、直前の要約を渡して各行を述語で終えるよう具体的に指示。エラー文言に問題の要約を含める。
  - 実機で「文字数を優先しすぎて助詞・述語が落ちる」出力を確認したため、プロンプト最優先項目に「助詞・述語を削って文字数を稼いではならない」と明記。
  - 再試行回数を 2→4（最大5回）に増やし、文字数超過時に「あとN文字削る」と具体的な差分を渡す。
  - JSON解析失敗・出力途切れの再試行では、長い修正指示を付けたプロンプトが更なる途切れを招くため元プロンプトへ戻す。再試行間隔を1秒→5秒に延ばす。
- **体言止めガードを厳格化（重要）**: 実機で `米マイクロソフトがコードレッドを宣言`（1行目だけ名詞止め）をガードが見逃していた。
  - `src/text_quality.py`: 既知の名詞語尾リスト方式をやめ、文末句読点を除いたうえで述語語尾と確認できない行はすべて体言止めとして扱う（フェイルクローズ）。
  - 既存テストの要約フィクスチャを各行述語終わりへ修正し、見逃した「〜を宣言」を不合格に含めるよう更新（全59件PASS）。

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
