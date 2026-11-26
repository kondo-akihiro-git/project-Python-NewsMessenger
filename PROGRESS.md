# PROGRESS.md

最終更新: 2026-11-26 (JST) — オンデバイス Apple Intelligence 切替・品質ガード強化・本番再試験成功

## 1. 全体の進捗状況
- **完了した Phase**: Phase 1〜8（すべて実装・検証済み）
- **テスト状況**: `pytest -q tests/` → **76件すべてPASS**
- **実統合検証**: 実RSS 108件取得 → 実記事1件を要約・レビュー承認 → 実Telegram送信成功（旧形式 message_id=21、新形式 message_id=22、オンデバイス版で再確認済み）
- **launchd**: インストール・有効化済み（`gui/501/com.aki.news-telegram`、5分間隔）
- **Git**: 公開リポジトリ `project-Python-NewsMessenger` にコミット・プッシュ済み（認証情報・DB・ログは除外）
- **未完了**: なし（任意の追加チューニングを除く）。iPhone側の通知表示はユーザー確認待ち。
- **既知の運用上の問題**: Apple Intelligence の JSON 出力が不安定（要約が55文字超、レビューの閉じ引用符が `」`、要約フィールド途中で出力終了など）。いずれも検証で拒否して再生成／通知しないため、誤通知は発生しない。プロンプトと再試行設計の強化で改善中（最大5回・文字数差分指示・体言止め時の述語指示・解析失敗時は元プロンプトへ戻す・間隔5秒）。
- **体言止めガードの見逃し修正**: 1行目だけ名詞止め（`〜を宣言`）を旧ガードが見逃していたため、述語と確認できない行はすべて不合格とする厳格な判定へ変更。既存フィクスチャも述語終わりに修正済み。
- **Apple Intelligence 混雑時の扱い**: 実機の `現在、リクエストを完了できません。` を利用制限として検出し、即座に失敗させて通知しない（有料APIへのフォールバックはしない）。検証中用いた既送信記事は `NOTIFIED` へ状態復元済み。
- **新ルールの実機検証記録（2026-11-26）**: 短縮専用プロンプトにより `米マイクロソフトがコードレッドを宣言しました / 大規模障害で対応を見直します / 復旧を1時間以内に目指します` を生成。**50文字・3行・体言止めなし**で6要素も包含。ただしその後のレビュー / 新記事の要約が Apple Intelligence の混雑（`現在、リクエストを完了できません。`）で失敗し、実送信は未達。フェイルクローズのため不正な通知は出ていない。launchd は有効のまま。

## 2. 完了した作業

### Phase 1
- Mac環境確認・Python実体パス確定（`/Users/aki/.pyenv/versions/3.10.12/bin/python3`）。
- Apple Intelligence 有効を確認（Siri ON / `ai.enhanced-siri canUse=True`）。設定変更は未実施。

### Phase 2（完了）
- `.env` に `TELEGRAM_BOT_TOKEN` と `TELEGRAM_CHAT_ID` を権限 `600` で保存済み。
- iPhone実機通知テスト完了（3行表示・通知音・リンク起動OK、要約本文へURL非混入を確定）。

### Phase 3（完了・実機検証済み）
- `config/prompts/{summarize,review,revise}.txt` を Python 側で一元管理。
- `src/ai_client.py`: `shortcuts run` ラッパー、JSON抽出・修復、バリデーション、エラー分類、`revise()`。
- 署名済みショートカット2件を生成し、「ショートカット」アプリへ登録済み（`shortcuts list` で確認）。
- `scripts/test_ai_shortcuts.py --repeat 1` 実機検証すべて成功（要約97秒 / レビュー193秒 / 誤要約FAIL検出 / 連続実行190秒）。

### Phase 4（完了）
- `src/config.py`, `src/models.py`, `src/rss_client.py`, `src/content_extractor.py`, `src/database.py`。
- 実RSS 3フィード計108件取得成功、本文1605文字抽出成功。
- **実 E2E で判明した不具合を修正**: `httpx` が302リダイレクトを追跡せず Yahoo!ニュースの本文取得が失敗 → `follow_redirects=True` を設定し、リダイレクト追跡テストを追加。

### Phase 5（完了）
- `src/pipeline.py`: RSS検出 → 本文取得 → 要約 → レビュー → （修正→再レビュー、最大2回）→ 承認/失敗状態。
- 失敗経路は必ず状態・イベントを記録し、APPROVED 以外は通知対象にしない。
- `tests/test_pipeline.py`（7件）で承認・修正ループ・不合格打ち切り・本文不足・要約失敗・情報不足を検証。

### Phase 6（完了）
- `src/notifier.py`: `build_notification_text()`（要約3行＋空行＋`元記事を読む\nURL`）、`ArticleNotifier`（APPROVED/NOTIFICATION_FAILED のみ、送信成功時のみ NOTIFIED、`notified_at` による二重送信防止、最大3回リトライ）。
- `main.py`: `--limit` / `--no-notify` / `--check-only` を備えたエントリポイント。`--check-only` 実行成功。
- `tests/test_notifier.py`（6件）。

### Phase 7（完了・インストール済み）
- `src/lock.py`: `fcntl.flock` による排他ロック。実行全体をロックし、重複起動時はスキップ。
- `launchd/com.aki.news-telegram.plist`: 5分間隔（`StartInterval=300`）、GUIユーザセッション前提、絶対パス指定。
- `scripts/install_launchd.sh install` を実行済み。`~/Library/LaunchAgents/com.aki.news-telegram.plist` に配置し、`launchctl enable gui/501/com.aki.news-telegram` で有効化（状態: enabled / run interval = 300秒）。
- `config/config.example.yaml` の `max_articles_per_run` を **5→1** へ変更（AI呼び出しが1〜3分のため）。
- `tests/test_lock.py`（2件）。

### Phase 8（完了）
- 全51テストPASS。
- 実ニュース E2E（`main.py --limit 1 --no-notify`）: RSS 108件新着登録 → 記事1件処理 → レビュー PASS → APPROVED。
- 実Telegram通知: 承認済み記事1件を送信し `message_id=21` / 状態 `NOTIFIED` を確認。

### 追加改善（2026-11-26: 通知の省略と要約の情報不足への対応）
- **問題**: 送信済み要約は65文字で、iPhone通知で省略された。また「コードレッド」「世界的な大規模障害」という核心キーワードと主体・理由が欠落していた。
- **プロンプト修正**: `summarize.txt` / `review.txt` / `revise.txt` を改訂。
  - 要約に「主体（固有名詞）・出来事名・対象・理由（〜を受けて）・対応（数値目標）・目的」の6要素を必須化。
  - 長さを **35〜55文字**（上限55、25文字未満不可）に明示。
  - レビュー不合格基準に「出来事名・主体・理由の欠落」「一般的な言い換えのみ」を追加。
- **通知修正**: `config.max_summary_chars` を 90→55 に変更。`notifier.py` が55文字超を送信前に拒否。
  - URL を本文に書かず、Telegram `text_link` entity で「元記事を読む」をリンク化（`telegram_client.send_message` に `entities` 追加）。表示文字数は最大55文字（要約45 + リンク文言10）。
- **検証**: 同じ Microsoft 記事で新要約 `米マイクロソフトがコードレッドを宣言 / 世界的な大規模障害を受けて / 復旧を1時間以内に終える目標`（45文字）を生成。短縮通知を実送信し `message_id=22` を確認。
- 既に送信済みの `message_id=21` は旧要約のまま（DB上の履歴）。新形式は今後の通知に適用される。

### 追加修正（2026-11-26: 体言止めの全面禁止・ドキュメント整備・Git公開）
- **ユーザー指摘**: 以前から「ニュースに体言止めを入れないで」と依頼していたが、採用要約 `復旧を1時間以内に終える目標` は体言止めだった。
- **修正**: `summarize.txt` / `review.txt` / `revise.txt` に「体言止めは1行でも禁止・述語で終える」と明記。`src/text_quality.py::has_taigen_dome()` を新設し、要約検証時と通知前の両方で拒否（フェイルクローズ）。
- **テスト**: 体言止め拒否のテストを追加し、既存フィクスチャも述語終わりへ修正 → **59件PASS**（日本語鉤括弧のJSON修復テスト含む）。
- **ドキュメント**: `docs/MASTER_DESIGN.md`（設計書原本）, `docs/DECISIONS.md`（判断記録）, `docs/SETUP_ANY_PC.md`（別Mac再開手順）を新規作成。`AGENTS.md` / `README.md` / `DESIGN.md` / `CHANGELOG.md` を更新。
- **Git**: `git init` → 初回コミット → 公開リポジトリ `project-Python-NewsMessenger` へプッシュ（認証情報・`data/`・`logs/` は除外）。

### 追加修正（2026-11-26: オンデバイス Apple Intelligence への切替・本番再試験）
- **背景**: Shortcuts Cloud モデルが15時間以上 `現在、リクエストを完了できません。` を返し続けた。新規ショートカットでも同一でコード側ではないと切り分け、ユーザー選択で管理者権限により Command Line Tools for Xcode 27.2 を導入。
- **切替**: `scripts/apple_llm_bridge.py` が `arch -arm64 /usr/bin/python3` で `apple_llm.AppleLLM(tier="device")` を呼び出す。ガイド付きJSON生成（スキーマ）でキー欠けを防止。有料APIフォールバックなし。
- **品質ガード強化**: 関連記事一覧をAI入力から除去（`trim_related_articles()`）。最低35文字・見出しの固有語1つ以上を機械確認し、欠ければフェイルクローズ（`missing_required_elements()`）。レビューは6要素を個別判定する厳格基準へ変更。
- **本番再試験**: 再試験中は launchd を無効化し、強い要約（19文字）が拒否されること、妥当な要約（51文字・述語終わり）が承認→Telegram送信されることを確認。完了後に launchd を再有効化。
- **テスト**: 76件PASS。

## 3. 未完了の作業
- なし。任意: Apple Intelligence の要約品質チューニング継続、実ニュース10件以上の総合テスト。

## 3-1. ドキュメント一覧（どのPCからでも参照可能）
- `README.md` / `DESIGN.md` / `AGENTS.md` / `CHANGELOG.md` / `PROGRESS.md`
- `docs/MASTER_DESIGN.md` — ユーザー提供の詳細設計書原本
- `docs/DECISIONS.md` — 設計判断・ユーザー指示の記録
- `docs/SETUP_ANY_PC.md` — 別Mac／AIエージェントの再開手順

## 4. 技術的留意点
1. **AI呼び出しが非常に遅い**: 要約約97秒 / レビュー約193秒。1記事最大3〜4呼び出し。5分間隔起動ではロックにより実質的に順次処理となる。
2. **フェイルクローズ**: 本文不足・AI情報不足・JSON破損・レビュー不合格・送信失敗はいずれも通知しない。
3. **`shortcuts` CLI は GUI ユーザセッション必要**: launchd は `~/Library/LaunchAgents/`（ユーザエージェント）に配置する。
4. **認証情報**: `.env`（権限600）のみ。`src/logger.py` のマスキングでログへの漏出を防止。
5. **設定ファイル**: `config/config.yaml` は未作成で `config.example.yaml` にフォールバック中。

## 5. 最後に成功したテスト・確認コマンド
- `/Users/aki/.pyenv/versions/3.10.12/bin/python3 -m pytest -q tests/` → `76 passed in 6.48s`
- `arch -arm64 /usr/bin/python3` で `apple_llm.probe()` → `device.available = true`
- `/Users/aki/.pyenv/versions/3.10.12/bin/python3 main.py --limit 1` → `RSS新着: 0件 / 処理: 1件 / 承認: 1件 / 通知送信: 1件`
- 実Telegram通知（オンデバイスAI版）: 要約 `ティボー・ソティオ氏は、dotsの提供を開始しました。個人向けプランでは月額3000円が適用されます。`（51文字・述語終わり・URLなし）→ 状態 `NOTIFIED`
- 弱い要約（19文字）→ `INSUFFICIENT_CONTENT`（最低文字数・固有語なしでフェイルクローズ）
- `launchctl print-disabled gui/501` → `"com.aki.news-telegram" => enabled`（再試験後にもどす）

## 6. 環境情報
- プロジェクトパス: `/Users/aki/.gemini/antigravity/scratch/news-telegram`
- Python: `/Users/aki/.pyenv/versions/3.10.12/bin/python3`
- Git: 公開リポジトリ `https://github.com/kondo-akihiro-git/project-Python-NewsMessenger`（ブランチ `main`）
