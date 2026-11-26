# PROGRESS.md

最終更新: 2026-11-26 (JST) — 低品質通知の再現と、2行・4W1H・単一イベントの厳格化

## 1. 全体の進捗状況
- **完了した Phase**: Phase 1〜8（すべて実装・検証済み）
- **テスト状況**: `pytest -q tests/` → **97件すべてPASS**
- **実統合検証**: 実RSS 108件取得 → 実記事1件を要約・レビュー承認 → 実Telegram送信成功（旧形式 message_id=21、新形式 message_id=22、オンデバイス版で再確認済み）
- **launchd**: インストール・有効化済み（`gui/501/com.aki.news-telegram`、5分間隔）
- **Git**: 公開リポジトリ `project-Python-NewsMessenger` にコミット・プッシュ済み（認証情報・DB・ログは除外）
- **通知形式**: 要約文章のみ（「元記事を読む」・URL・リンク entity なし）。初見キーワードには本文ベースの説明を必ず添える。
- **未完了**: なし（任意の追加チューニングを除く）。新しい通知のiPhone表示はユーザー確認待ち。
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
- `src/notifier.py`: `build_notification_text()`（当初は要約3行＋空行＋`元記事を読む\nURL`。**現在は要約のみ**）、`ArticleNotifier`（APPROVED/NOTIFICATION_FAILED のみ、送信成功時のみ NOTIFIED、`notified_at` による二重送信防止、最大3回リトライ）。
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
  - 当時は URL を本文に書かず、Telegram `text_link` entity で「元記事を読む」をリンク化していた。**2026-11-26 にユーザー指示で廃止**（下記追加修正を参照）。
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

### 追加修正（2026-11-26: 要約のみ通知・初見キーワード説明の必須化）
- **ユーザー指示**: 以前から伝えていた「元記事を読む」リンク構成の廃止と、小学生が初見のキーワードへの説明追加を反映。
- **通知**: `src/notifier.py` が要約本文のみを返すように変更。`build_link_entity()`・`text_link` entity・リンク文言を全廃。送信前検査（55文字／述語／必須要素）は維持（行数は当時3行、現在は下記で2行）。
- **説明の必須化**: `summarize.txt` に「用語＋という＋記事の意味＋述語」の形と55文字内の並べ方例を追加。`REVIEW_SCHEMA` に `keyword_explanation_ok` / `unexplained_terms` を追加し、PASS でも説明不足なら FAIL に上書き。`revise.txt` / `shorten.txt` が説明を残すよう修正。
- **品質ガード**: 見出しの固有語2つ以上（主体＋出来事名）と、不自然な述語（「〜としました」等）を拒否。
- **実機検証**: dots の記事で `OpenAIはdotsという常時稼働型エージェントをPlusプランで個人向けに提供します。`（45文字・説明入り）を承認し Telegram 実送信。通知本文は要約のみ。テスト79件PASS。launchd は再有効化済み。

### 追加修正（2026-11-26: 低品質通知の再現と 2行・4W1H・単一イベントの厳格化）
- **対象記事**: `https://www.itmedia.co.jp/news/article/2611/24/2000003405/`（タイトル: `月に3度の障害……揺らぐドコモの信頼　通信品質が安定しない理由は？`）。問題の通知は `NTTドコモへの信頼が揺らぎつつあります。藤井総務省が原因究明をもとめました。`。
- **再現試験（3回）**:
  1. 1回目: `NTTドコモは11月5日、11日、16日に5G SAで通信障害が発生しました。総務相は原因究明を求めました。` を AI が PASS。5G SA未説明・氏名なしで不合格と判定しガードを追加。
  2. 2回目: `11月5日、11日、16日にNTTドコモで3度の通信障害が発生しました。藤井総務相が原因究明を求めました。` を AI が PASS。独立2イベント・同一文にWHO/WHAT無しで不合格と判定し、複数イベントの機械ガードを追加。
  3. 3回目: 低品質要約2件を機械ガードで拒否。再生成中にオンデバイス模型のコンテキスト超過（4098〜4099トークン / 上限4096）が発生。ITmedia フッターも除去対象に追加（本文3113→1457文字）。最終的に `藤井総務相がNTTドコモで発生した3度の通信障害の原因究明を求めました。`（36文字・1行）が承認。
  4. 4〜5回目: フッター除去だけではプロンプト全体が4019文字で超過し、71文字の要約が繰り返されて `SUMMARY_FAILED`（フェイルクローズ）。`src/pipeline.py` でAI渡し本文を900文字→450文字へ制限（ガイド付きJSONのスキーマオーバーヘッドを実測し、要約プロンプト全体を約3010文字へ短縮）。450文字にもリード文と「5G SAとは…」の説明が含まれる。
- **追加ガード**: 2行超、根拠なき評価、同一文に無いWHO/WHAT、初見用語未説明、役職のみ／誤役職表記、独立した複数イベントを `src/text_quality.py` と `src/ai_client.py` の両方で拒否。AIレビューの新フラグが false なら PASS を FAIL へ上書き。
- **最終結果（7回目の再現試験）**: 独立2イベント（「障害が発生しました。総務相が原因究明を求めました。」）と同一文にWHO/WHATが無い要約を連続拒否。AIは83文字の要約を繰り返したため、最終状態は `SUMMARY_FAILED`（要約なし・レビューなし・通知なし）。**低品質通知は再現試験で一切送信されなかった**。
- **検証**: `pytest -q tests/` は97件PASS。`main.py --check-only` OK。通知テキスト検査は既承認要約で通過確認。launchd は再試験中のみ無効化し、完了後に再有効化済み。
- **送信済み通知は取り消し不可**。目的は同種要約の今後の通知停止であり、低品質パターンはすべて再現試験で拒否された。

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
- `/Users/aki/.pyenv/versions/3.10.12/bin/python3 -m pytest -q tests/` → `79 passed in 6.58s`
- `arch -arm64 /usr/bin/python3` で `apple_llm.probe()` → `device.available = true`
- `/Users/aki/.pyenv/versions/3.10.12/bin/python3 main.py --limit 1 --no-notify` → dots 記事がレビュー承認（45文字・説明入り）
- `/Users/aki/.pyenv/versions/3.10.12/bin/python3 main.py --limit 1` → `通知送信: 1件`、状態 `NOTIFIED`
- 実Telegram通知（要約のみ・リンクなし）: `OpenAIはdotsという常時稼働型エージェントをPlusプランで個人向けに提供します。`
- 弱い要約（19文字／固有語1つ／不自然な述語）→ フェイルクローズで拒否
- `launchctl print-disabled gui/501` → `"com.aki.news-telegram" => enabled`（再試験後にもどす）

## 6. 環境情報
- プロジェクトパス: `/Users/aki/.gemini/antigravity/scratch/news-telegram`
- Python: `/Users/aki/.pyenv/versions/3.10.12/bin/python3`
- Git: 公開リポジトリ `https://github.com/kondo-akihiro-git/project-Python-NewsMessenger`（ブランチ `main`）
