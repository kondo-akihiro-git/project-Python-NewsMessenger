# DESIGN.md（設計書と実装・環境との差分記録）

本ファイルはユーザー提供の「ニュース自動取得・AI要約・品質レビュー・Telegram通知システム 詳細設計書」と、実際のMac環境・実装との差分および技術的検証事項を記録するためのドキュメントです。

## Phase 1 環境調査で確認された仕様・差分

1. **開発・実行プロジェクトパス**
   - `/Users/aki/.gemini/antigravity/scratch/news-telegram` にプロジェクトを作成。
2. **OS・ハードウェア環境**
   - macOS `27.2.1` (Build `26B131`) / `arm64` / `Apple M1` / システム言語 `ja-JP`
   - Apple Silicon + macOS 言語設定が日本語であるため、Apple Intelligence の基盤要件を満たす。ただし「システム設定 > Apple IntelligenceとSiri」の有効化状態およびショートカット内「モデルを使用」アクションの挙動は実機検証が必要。
3. **Python 環境と複数バージョン混在への対応**
   - `/Users/aki/.pyenv/shims/python3` の実体バイナリは `/Users/aki/.pyenv/versions/3.10.12/bin/python3`（`Python 3.10.12`）であることを確認。
   - `beautifulsoup4`, `lxml`, `feedparser`, `httpx`, `requests`, `PyYAML`, `pytest`, `pydantic`, `tenacity`, `python-dotenv` 等が導入済み。
   - システム内に `/opt/anaconda3/bin/python3`, `/Library/Frameworks/Python.framework/Versions/3.9/bin/python3`, `/usr/local/bin/python3`, `/usr/bin/python3` が併存するため、`launchd` 設定および実行スクリプトでは `/Users/aki/.pyenv/versions/3.10.12/bin/python3` を明示指定して解決する。
4. **`shortcuts` CLI の挙動**
   - `/usr/bin/shortcuts` が利用可能（`run`, `list`, `view`, `sign` サブコマンドあり）。
   - `shortcuts run` は `-i/--input-path`, `-o/--output-path`, `--output-type` をサポート。
   - 注意: 制限されたサンドボックス環境内から `shortcuts` を呼ぶと `Couldn’t communicate with a helper application.` エラーとなるため、ショートカット関連の実機テスト・実行は通常のユーザ権限／サンドボックス外で行う必要がある。`launchd` も `~/Library/LaunchAgents/`（GUIユーザセッション）で実行する必要がある。
5. **Telegram アプリ環境・Phase 2 実機通知テスト結果（2026-11-25 確認）**
   - Mac 側の `/Applications/Telegram*.app` は未検出。ただし通知送信は Mac から Telegram Bot API（HTTPS）を叩く構成であり、iPhone 側に Telegram アプリがあれば運用可能。
   - iPhone 実機での通知テスト結果に基づき、以下の仕様を確定:
     - 通知音・リンク起動は正常。
     - iPhone の通知センター上での全文展開はできないため、**通知センター上で1回で見切れる長さとして「3行以内（1行17〜18文字程度、合計45〜55文字目安）」を目標値**に採用する（設計書の初期目安4行から3行へ最適化）。
     - **要約本文（`summary`）には URL・リンクを一切含めない。** 元記事リンクは Telegram `text_link` entity で「元記事を読む」に付与し、URL文字列は本文に出さない（後日改訂。下記「追加改善」を参照）。当初は末尾に空行を挟んで分離配置していた（`元記事を読む\nhttps://...`）。

## Phase 3 実装方針（Apple Intelligence × ショートカット連携）

1. **ショートカットとプロンプトの役割分離**
   - プロンプト本文は `config/prompts/`（`summarize.txt`, `review.txt`, `revise.txt`）で Python 側が一元管理する。
   - macOS ショートカット（`News_Summarize`, `News_Review`）は「ショートカット入力のテキストを受け取る → Apple Intelligence『モデルを使用』へ渡す → 出力テキストを返す」最小構成とする。これにより、プロンプト調整時に毎回ショートカットGUIを編集する必要をなくし、Git管理・テスト性を高める。

## Phase 3 実機検証で判明した仕様・差分（2026-11-25）

1. **ショートカットの登録状態**
    - 署名済み `.shortcut` を「ショートカット」アプリへ追加済み。`shortcuts list` で `News_Summarize` / `News_Review` の両方を確認。
    - 旧 `PROGRESS.md` の「インポート待ち」記載は実態と異なるため更新済み。
2. **出力ファイルの非同期書き込み**
    - `shortcuts run` のプロセス終了後も出力ファイルへの書き込みが続き、完了まで最大数十秒かかることがある。
    - 0.6秒のサイズ安定判定では JSON の途中（例: `{"status":"OK","summary":"...",` で終了）を読んでいた。
    - 修正: `require_json_object=True` で括弧の対応と文字列終端を検査し、完全な JSON オブジェクトになるまで待機。`stable_for=2.0` / `max_wait=90.0`。
3. **JSON文字列内の生改行**
    - Apple Intelligence は `summary` 値にエスケープされていない改行を挿入する（`json.loads` は失敗）。
    - 修正: `repair_json_string_control_chars()` で文字列内のみ `\n` 等へエスケープしてから解析。元文字列は `raw_text` として保持する。
    - 一部の実行では待機上限を超えて途中出力のままになるが、リトライで回避し、最終的に不正出力は通知しない（フェイルクローズ）。
4. **処理時間（実測）**
    - 要約: 約97秒 / レビュー: 約193秒 / 連続実行: 約190秒。
    - 記事1件あたり最大3回の AI 呼び出しで 5〜10分。5分間隔の `launchd` と併用するには**排他ロックが必須**。
    - `max_articles_per_run: 5` は実運用では過大なため、Phase 7 で 1〜2件へ見直す。
5. **要約品質の傾向**
    - Apple Intelligence は名詞羅列・重要事実欠落の要約を出力しやすく、レビューが FAIL を返すケースが多い。
    - 設計どおり FAIL 時は通知しない。件数を増やしたい場合は `config/prompts/` を調整する（ショートカットの再生成は不要）。

## Phase 4 実装方針・差分（2026-11-25）

1. **RSS取得（`src/rss_client.py`）**
    - `httpx` で取得、`feedparser` で解析。`robots.txt` を厳密に解釈するライブラリは導入せず、取得間隔・同時数1・タイムアウト・リトライで負荷を制御する。
    - フィード単位で結果を分離し、1件の失敗が他フィードに影響しないようにした。
2. **記事本文抽出（`src/content_extractor.py`）**
    - `readability` は未導入（追加依存を避ける）。`BeautifulSoup(lxml)` で `article` / `[itemprop=articleBody]` / `.article-body` 等を候補とし、テキスト量が最大の要素を本文とする。
    - `script`, `style`, `nav`, `header`, `footer`, `aside`, `form`, `iframe` 等は抽出前に除去。
    - 本文文字数は空白を除いてカウント。`min_chars_guideline=400` は「目安」とし、フェイルクローズ用の絶対フロアは `max(80, guideline/4)`（=100文字）。フロア未満は `is_sufficient=False` として通知対象外。
    - 同一段落の連続重複は除去。
3. **SQLite（`src/database.py`）**
    - `data/news.db`（WALモード）。記事キーは RSS GUID → 正規化URL → URL+見出しハッシュの順で生成し、`INSERT OR IGNORE` で冪等性を担保。
    - 追跡パラメータ（`utm_*`, `fbclid`, `gclid`, `ref`, `spm` 等）とフラグメントは URL 正規化時に除去。
    - 状態遷移は `article_events` に記録し、「結果不明」の追跡を可能にした。
4. **実ネットワーク検証結果**
    - Yahoo! ITトピックス 8件 / Yahoo! ITmedia配信 50件 / ITmedia NEWS 50件、取得成功。
    - 本文抽出例: 1605文字、`is_sufficient=True` / `meets_guideline=True`。
5. **Git 状態**
    - プロジェクトは Git リポジトリではない（`.git` なし）。ブランチ・履歴・未コミット変更は存在しない。`.gitignore` は準備済み。

## Phase 5〜8 実装方針・検証結果（2026-11-25）

1. **要約・レビュー・修正ループ（`src/pipeline.py`）**
    - 1記事のフロー: `FETCHING` → `FETCHED` → `SUMMARIZING` → `REVIEWING` → （FAIL なら `revise()` → 再レビュー、最大2回）→ `APPROVED`。
    - 失敗状態: `FETCH_FAILED` / `INSUFFICIENT_CONTENT` / `SUMMARY_FAILED` / `REVIEW_FAILED` / `REVIEW_REJECTED`。いずれも通知しない。
    - 修正要約は専用ショートカットを追加せず、汎用パススルー構成の `News_Summarize` に `revise.txt` を渡して実行する。
2. **通知（`src/notifier.py`）**
    - 通知テキストは「要約3行（URLなし）＋空行＋`元記事を読む\nURL`」。Phase 2 の実機検証結果を反映。
    - `APPROVED` と `NOTIFICATION_FAILED` のみを対象とし、`notified_at` が設定済みの記事は再送しない。送信成功時のみ `NOTIFIED` を設定。
    - 要約が空・URL混入・3行超えは送信前のバリデーションで拒否（フェイルクローズ）。
3. **排他制御・launchd（`src/lock.py`, `launchd/`）**
    - `fcntl.flock` による非ブロッキングファイルロック（`data/news.lock`）。ロック取得不可時は今回の実行をスキップ。
    - `launchd/com.aki.news-telegram.plist` は GUI ユーザセッション（`~/Library/LaunchAgents/`）前提。`StartInterval=300`、Python とスクリプトは絶対パス指定。
    - 実測した AI 処理時間（要約97秒 / レビュー193秒）に基づき、`max_articles_per_run` を 5→1 に変更。
    - インストールは `scripts/install_launchd.sh install`。**インストール・有効化済み**（`gui/501/com.aki.news-telegram`、5分間隔）。編集中は一時的に disable し、完了後に enable した。
4. **実 E2E で発見・修正した不具合**
    - `httpx` の既定ではリダイレクトを追跡せず、Yahoo!ニュースの `pickup` URL（302）で本文取得が失敗した。
    - `RSSClient` / `ContentExtractor` の両方に `follow_redirects=True` を設定し、リダイレクト追跡の単体テストを追加。
5. **最終検証結果**
    - `pytest -q tests/`: 51件PASS（その後、体言止めガード追加で58件PASS）。
    - `main.py --check-only`: ショートカット登録・設定・DB初期化を確認。
    - `main.py --limit 1 --no-notify`: RSS新着108件、記事1件をレビューPASS → `APPROVED`。
    - 実Telegram通知: `message_id=21`、`NOTIFIED` を確認。
    - 状態集計: `DISCOVERED=106`, `FETCH_FAILED=1`, `NOTIFIED=1`。

## 追加改善: 通知の省略と要約の情報不足（2026-11-26）

1. **判明した問題**
    - 初回の実通知は要約65文字＋URL（全体126文字）で、iPhone の通知プレビューで省略された。
    - 要約が「深刻なアラートを出し、仕組みを見直す」といった一般化のみで、何についてのアラートか／なぜ対応するかが分からなかった。
2. **要約プロンプトの改訂**
    - 6要素（主体／出来事名／対象／理由／対応／目的）を必須化。固有名詞・出来事名は記事の表記を保持し、勝手に言い換えない。
    - 文字数を35〜55文字と明示（25文字未満不可、55文字超不可）。重要キーワードを残すために低優先度情報を削る。
    - レビュー基準に「出来事名・主体・理由の欠落」「一般的な言い換えのみ」「関連記事/アンケート/AI Q&A の誤使用」を追加。
3. **通知フォーマットの改訂**
    - `max_summary_chars` を 90→55 に変更し、`build_notification_text()` が55文字超を送信前に拒否。
    - URL をテキストに含めず、Telegram `message_entity` の `text_link` で「元記事を読む」に付与。offset/length は UTF-16 コード単位で算出。
    - 表示テキストは要約（最大55文字）＋「元記事を読む」（6文字）と改行2つの、最大63文字に収まる。
4. **検証結果**
    - 同じ Microsoft 記事で `米マイクロソフトがコードレッドを宣言 / 世界的な大規模障害を受けて / 復旧を1時間以内に終える目標`（45文字）を生成。
    - 新形式の通知を実送信し `message_id=22` を確認（表示テキスト55文字、リンクは entity で付与）。
    - 55文字超の要約は最初の試行でリトライされ、最終的に45文字の要約が採用された（フェイルクローズが機能）。

## 追加修正: 体言止めの全面禁止とドキュメント／Git公開（2026-11-26）

1. **ユーザー指摘**
    - 設計書 5-1 / 5-7 / 7-2 で「体言止めを連続させない」としていたが、ユーザーは「体言止めはやめて」と改めて明示。
    - 採用済みの要約 `復旧を1時間以内に終える目標` は体言止めであり、設計意図に反していた。
2. **修正内容**
    - `config/prompts/{summarize,review,revise}.txt`: 「体言止めは1行でも絶対に禁止」と明記。各行は述語で終えること、名詞止めはレビュー不合格であることを明文化。
    - `src/text_quality.py` を新設し、`has_taigen_dome()` で一般的な名詞語尾（目標／可能性／対応 等）を検出。
    - `src/ai_client.py`: 要約JSON検証時に体言止めを検出したら `InvalidAIResponseError`（リトライ対象）。
    - `src/notifier.py`: 送信前の最終検査でも体言止めを拒否（既存DB要約を含むフェイルクローズ）。
    - `tests/test_ai_client.py` / `tests/test_notifier.py`: 体言止め拒否のテストを追加。既存フィクスチャの要約も述語終わりへ修正。**58件PASS**。
3. **ドキュメント・Git**
    - `docs/MASTER_DESIGN.md`: ユーザー提供の設計書原本を全文保存。
    - `docs/DECISIONS.md`: これまでの判断・認識（体言止め、6要素、通知仕様、有料API禁止、launchd等）を記録。
    - `docs/SETUP_ANY_PC.md`: 別Mac／AIエージェントがクローンから再開するための手順を記録。
    - `AGENTS.md` / `README.md` / `CHANGELOG.md` / `PROGRESS.md` を更新し、公開リポジトリ `project-Python-NewsMessenger` で管理。
4. **実機検証で判明した出力待ちの不足**
    - 新プロンプトで要約を実行すると、AIの生出力が `summary` の直後（`"summary":"...",`）で切れて JSON 解析に失敗した。要約本文自体は述語終わり（`〜掲げました。`）で体言止めは解消されていた。
    - 原因: `shortcuts run` プロセス終了後も「モデルを使用」の出力ファイルへの書き込みが続くが、待ち上限が90秒では不足していた。
    - 修正: `ShortcutAIClient` の既定 `timeout_seconds` を 60→240秒、`max_wait` を 90→180秒に延長。不完全JSONの場合はさらに同一ファイルを再ポーリングする二段構えに変更。`config/config.example.yaml` も `ai.timeout_seconds: 240` に更新。
    - テストは引き続きPASS（現時点で56件）。
5. **JSON切り上げへのフェイルクローズ対応**
    - 待ち延長後も3回とも `summary` 直後で出力が終わるため、`extract_json_object()` に復旧候補を追加。
    - 復旧では事実を補わず、欠落メタフィールドを `key_facts: []` / `reason: 不明` / `location: 不明` / `numbers: []` / `uncertainties: [AI出力が途中で終了したため詳細は不明]` と明示する。要約本文はAIの実出力を保持。
    - 体言止め・文字数・空文字の検査（`validate_summary_payload()`）とレビューAIの事実照合は従来どおり実施。復旧だけでは通知は通らず、フェイルクローズは維持。
    - 実出力の要約は66文字で `max_summary_chars: 55` を超過するため、この記事単体では依然として通知されない（既知問題として `PROGRESS.md` に記録）。
    - 切り上げの原因の一つとして、プロンプトの出力形式にコード側で未使用の `event` / `target` / `action` / `purpose` が含まれJSONが長かった点を特定。`summarize.txt` / `revise.txt` から削除し、6要素は要約本文に含めるルールは維持。
    - 文字数・行数超過で再試行する場合、次回試行のプロンプトにのみ「上限以内に短くしてください」という再生成指示を付与。事実の追加は指示せず、述語で終えるルールとレビューによる事実照合は維持。
    - テスト58件PASS。
