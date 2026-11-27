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
     - iPhone の通知センター上での全文展開はできないため、**通知センター上で1回で見切れる長さとして「3行以内（1行17〜18文字程度、合計45〜55文字目安）」を目標値**に採用する（設計書の初期目安4行→3行→**現在は2行**。現在は「55文字以内・最大2行、1行目に主要ニュース、2行目は補足のみ」を適用）。
     - **要約本文（`summary`）には URL・リンクを一切含めない。** 元記事リンクは当時 Telegram `text_link` entity で「元記事を読む」に付与していたが、**2026-11-26 のユーザー指示で完全廃止**（下記「追加修正」を参照）。当初は末尾に空行を挟んで分離配置していた（`元記事を読む\nhttps://...`）。

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
    - 通知テキストは当時「要約3行（URLなし）＋空行＋`元記事を読む\nURL`」だった。**現在は要約のみ**（下記「追加修正」を参照）。
    - `APPROVED` と `NOTIFICATION_FAILED` のみを対象とし、`notified_at` が設定済みの記事は再送しない。送信成功時のみ `NOTIFIED` を設定。
    - 要約が空・URL混入・2行超えは送信前のバリデーションで拒否（フェイルクローズ）。
3. **排他制御・launchd（`src/lock.py`, `launchd/`）**
    - `fcntl.flock` による非ブロッキングファイルロック（`data/news.lock`）。ロック取得不可時は今回の実行をスキップ。
    - `launchd/com.aki.news-telegram.plist` は GUI ユーザセッション（`~/Library/LaunchAgents/`）前提。`StartInterval=300`、Python とスクリプトは絶対パス指定。
    - 実測した AI 処理時間（要約97秒 / レビュー193秒）に基づき、`max_articles_per_run` を 5→1 に変更。
    - インストールは `scripts/install_launchd.sh install`。**インストール・有効化済み**（`gui/501/com.aki.news-telegram`、5分間隔）。編集中は一時的に disable し、完了後に enable した。
4. **実 E2E で発見・修正した不具合**
    - `httpx` の既定ではリダイレクトを追跡せず、Yahoo!ニュースの `pickup` URL（302）で本文取得が失敗した。
    - `RSSClient` / `ContentExtractor` の両方に `follow_redirects=True` を設定し、リダイレクト追跡の単体テストを追加。
5. **最終検証結果**
    - `pytest -q tests/`: 51件PASS（その後、体言止めガード追加で59件PASS）。
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
    - 当時は URL をテキストに含めず、Telegram `message_entity` の `text_link` で「元記事を読む」に付与していた。**現在は entity 自体を送信しない**。
    - 表示テキストは当時 要約（最大55文字）＋「元記事を読む」（6文字）で最大63文字だった。**現在は要約のみ（最大55文字）**。
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
    - `tests/test_ai_client.py` / `tests/test_notifier.py`: 体言止め拒否のテストを追加。既存フィクスチャの要約も述語終わりへ修正。**59件PASS**。
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
    - ユーザーから「55文字も6要素もどちらも絶対に守る。言い換えて両立させるのがAIの役目」と明示されたため、上限緩和・要素削減は行わない。
    - `summarize.txt` / `revise.txt`: 文字数ルールを冒頭へ移動し、行ごとの目安（20/19/16文字）と「事実を削らず言い換える」例を追加。6要素は短い言い換えで残すと明記。
    - 再試行時は直前の要約本文と実文字数をプロンプトに渡し、6要素と述語を残した言い換えを具体的に指示。体言止め検出時も同様に述語付与を指示。
    - 実機で「文字数優先で助詞・述語が落ちる」出力（「コードレッド宣言」「対応見直し」）を確認したため、プロンプト最優先項目に「助詞・述語を削って文字数を稼いではならない」と明記。
    - 再試行は最大5回とし、文字数超過時は「あとN文字削る」差分を渡す。
    - JSON解析失敗や出力途切れでは修正指示付きプロンプトが更なる途切れを招くため元プロンプトへ戻し、再試行間隔を5秒に延ばす。
6. **体言止めガードの厳格化（2026-11-26、重要）**
    - 45文字・3行で通知まで通った要約の1行目 `米マイクロソフトがコードレッドを宣言` は名詞止めだったが、旧ガードは `宣言` が既知語尾リストに無く見逃していた。
    - `src/text_quality.py` を改訂: 文末の句読点を除去後、述語語尾（です／ます／しました／た／る／ない 等）と確認できない行はすべて体言止めとして不合格にする。未知の語尾は安全側に倒す。
    - テストの要約フィクスチャも各行述語終わりへ修正。`〜を宣言` の見逃しケースを不合格テストへ追加。**59件PASS**。
    - `config/prompts/shorten.txt`（短縮専用プロンプト）を追加。再生成では元プロンプトへの長文追記ではなく、前回要約・記事本文・6要素と述語のルールだけを渡す。`ai_client.summarize()` が `retry_template` を受け取り `{{PREVIOUS_SUMMARY}}` 等を差し替える。テスト60件PASS。
7. **混雑時クールダウン（2026-11-26）**
    - `src/ai_cooldown.py` が `data/ai_cooldown.json` に解除時刻を保存。`main.py` は残り時間がある間は要約・レビューをスキップし、既承認記事の通知は続ける。
    - 混雑を検出した runs は `ai_cooldown.start()` で残り時間に `ai.cooldown_seconds`（既定900秒）を追加。再検出では累積するため解除が早まりすぎない。
    - テスト66件PASS。

## 追加修正: オンデバイス Apple Intelligence への切替（2026-11-26）

1. **背景**
   - macOS ショートカットの Cloud モデルが15時間以上 `現在、リクエストを完了できません。` を返し続けた。新規作成ショートカットでも同一エラーで、コード側の問題ではないと切り分け。
   - Shortcuts のモデルボタンは AX で `VALUE_BEFORE=クラウド` と確認できたが、選択肢の列挙・切替が自動化できなかった。
   - ユーザー選択により管理者権限で **Command Line Tools for Xcode 27.2** を導入。`FoundationModels` モジュールが利用可能になり、オンデバイスモデル（`AFM 3 Core`）の生成を確認。
2. **実装**
   - `scripts/apple_llm_bridge.py`: `arch -arm64 /usr/bin/python3` で起動し、`apple_llm.AppleLLM(tier="device")` を呼ぶ。プロンプトは標準入力、出力は標準出力。外部APIは使用しない。
   - `--schema` 指定時は `llm.json()` によるガイド付き生成を使い、JSONの形を機械的に固定する。キー欠けは型だけ補完し、事実は追加しない。
   - `src/ai_client.py`: `OnDeviceAIClient(ShortcutAIClient)` を追加。要約・修正には `SUMMARY_SCHEMA`、レビューには `REVIEW_SCHEMA` を渡す。検証・リトライ・フェイルクローズは既存ロジックを再利用。
   - `src/pipeline.py`: 既定の AI クライアントを `OnDeviceAIClient` に変更。`main.py --check-only` もショートカット一覧ではなく `probe()["device"]["available"]` を確認する。
3. **品質ガードの追加**
   - オンデバイスモデルは「6要素あり」と誤レビューすることが実機で確認されたため、機械ガードを追加。
   - `src/text_quality.py::trim_related_articles()`: 記事末尾の関連記事一覧をプロンプトから除去（別記事の事実混入を防止）。
   - 同 `missing_required_elements()`: 最低35文字未満、または見出しの固有語が1つも要約に無い場合はフェイルクローズ。理由／目的は記事に明記がない場合もあるため AI レビューに委ねる。
   - `config/prompts/shorten.txt`: 短縮時に35文字未満へ落とさない旨を明記。
4. **実機検証**
   - `pytest -q tests/`: **76件PASS**。
   - `main.py --limit 1` で実RSS記事をレビュー承認 → Telegram送信成功（`NOTIFIED`）。要約は51文字・述語終わり・URLなし。
   - 弱い要約（19文字）は機械ガードで通知前に拒否されることを確認。
   - launchd は再試験中のみ無効化し、終了後に再有効化済み。

## 追加修正: 通知を要約のみへ＋初見キーワード説明の必須化（2026-11-26）

1. **ユーザー指示による仕様変更（設計書原本より優先）**
   - 通知から「元記事を読む」のリンク文言・URL・`text_link` entity をすべて廃止し、**要約文章のみ**を送信する。
   - 小学生が最初に読んで初見のキーワードには、記事本文に書かれた意味だけを使った短い説明を要約内に必ず添える。55文字・6要素・述語終わりは維持する（行数は当時3行、下記の厳格化で現在は2行）。
2. **実装**
   - `src/notifier.py`: `build_notification_text()` が要約本文のみを返す。`build_link_entity()` と `_LINK_LABEL` を削除し、`send_message()` から `entities` を外した。
   - `config/prompts/summarize.txt`: 「用語＋という＋記事の意味＋述語」の形を必須化。55文字内の並べ方の例と、不自然な述語（「〜としました」）の禁止を明記。
   - `config/prompts/review.txt` / `REVIEW_SCHEMA`: `keyword_explanation_ok`（真偽）と `unexplained_terms`（配列）を追加。説明不足が応答された場合は AI が PASS を返しても `validate_review_payload()` が FAIL に上書きする（フェイルクローズ）。
   - `config/prompts/revise.txt` / `shorten.txt`: 説明を削って文字数を稼ぐこと、不自然な述語、主体の欠落を禁止。`UNEXPLAINED_TERMS` を修正プロンプトへ渡す。
   - `src/text_quality.py`: 見出しの固有語2つ以上（主体と出来事名の手掛かり）を要求。`〜としました` 等の不自然な述語も拒否。
3. **実機検証**
   - `pytest -q tests/`: **79件PASS**。
   - dots の記事で `OpenAIはdotsという常時稼働型エージェントをPlusプランで個人向けに提供します。`（45文字・説明入り・自然な述語）を生成・レビュー承認・Telegram送信成功。通知本文は要約のみでリンクなし。
   - 旧 `docs/MASTER_DESIGN.md` のリンク付き通知形式は、ユーザー指示により無効化した（判断履歴は `docs/DECISIONS.md`）。

## 追加修正: 2行・4W1H・単一イベントの厳格化（2026-11-26）

1. **判明した問題**
   - 実送信済みの `NTTドコモへの信頼が揺らぎつつあります。藤井総務省が原因究明をもとめました。` は、背景イベントの欠落・WHO/WHATの欠落・曖昧表現・複数イベントの混入・3行超過の可能性があった。
   - 再現試験では AI が `NTTドコモは11月5日、11日、16日に5G SAで通信障害が発生しました。総務相は原因究明を求めました。` を PASS したが、**5G SA の説明なし・「総務相」に氏名なし・2つの独立イベント**だったため不合格。
2. **仕様変更**
   - 要約は **55文字以内・最大2行**。1行目に最も重要なニュース、2行目はその補足のみ。
   - 6要素に加えて **4W1H** を必須化。WHO（固有名詞の主体・氏名）と WHAT（何が起きたか。何の原因／何への対応か）は絶対に落とさない。
   - 根拠のない評価表現（「信頼が揺らぐ」等）は、背景イベントと根拠が要約内に無ければ不合格。
   - 記事に複数のイベントがあっても **主要イベント1つに絞る**。別主体の行動を独立した文にせず、従属節として1文にまとめる。
   - 初見キーワード（5G SA 等）は記事本文のみを根拠に「という」で説明する。
3. **実装**
   - `config/config.example.yaml` / `src/config.py`: `max_summary_lines` を 3→2、既定 `max_summary_chars` を 90→55 に変更。
   - `config/prompts/{summarize,review,revise,shorten}.txt`: 2行構成・1主要イベント・4W1H・根拠なき評価禁止・氏名必須・初見用語説明を明記。レビュー出力に `concrete_basis_ok` / `four_w_one_h_ok` / `single_event_ok` / `missing_4w1h` を追加。
   - `src/ai_client.py`: `REVIEW_SCHEMA` と `ReviewResult` に上記4項目を追加。AI が PASS を返しても不合格フラグがあれば FAIL へ上書きし、理由にも反映する。要約JSON検証でも曖昧評価・WHO/WHAT欠落・初見用語未説明・役職のみを拒否し、再生成指示を出す。
   - `src/text_quality.py`: 2行超、根拠なき評価、WHO/WHAT欠落、初見用語未説明、役職のみ／誤った役職表記を機械ガードへ追加。
   - `src/pipeline.py`: レビューの新フラグを修正プロンプトへ渡す。`src/notifier.py`: 送信前検査を2行へ統一。
   - `src/text_quality.py::trim_related_articles()`: ITmedia の著作権行・アクセスランキング以降も除去（実記事3113→1457文字）。
   - `src/pipeline.py::_limited_body()`: AIへ渡す本文を文境界で350文字までに制限（当初450文字だったが、プロンプト強化後に4099トークンで超過したため短縮）。ガイド付きJSONのスキーマ分を含め、要約プロンプト4019→約2986文字とし、オンデバイス模型の4096トークン超過を防ぐ。350文字にリード文と用語説明は含まれる。
   - `src/ai_client.py` / `config/prompts/shorten.txt`: 品質ガード（初見用語・複数イベント・役職のみ等）に抵触した場合も、元の長いプロンプトへ追記せず短い専用プロンプトへ失敗理由を渡して再生成する。
   - `scripts/apple_llm_bridge.py`: ガイド付き生成で品質真偽値が欠けた場合は `false` で補完しフェイルクローズする旨を明記。
4. **検証**
   - `pytest -q tests/`: **102件PASS**（低品質要約の拒否・再現ケース・新フラグの上書き・複数イベントの機械拒否・ITmediaフッター除去・本文350文字制限・品質失敗時の短縮再生成・修正ループの再試行を追加）。
   - `main.py --check-only`: OK。
   - 再現試験の最終結果は `PROGRESS.md` に記録する（承認された場合のみ実送信対象）。

## 19. 品質不合格時の再生成ループ（2026-11-27）

- **ユーザー指摘**: 「基準を満たす要約が出ないことで通知件数が減ることはない」認識。従来の固定回数打ち切りはこれと矛盾していた。
- **方針**: 品質ガード／AIレビューで不合格になった要約は、基準を満たすまで再生成・再レビューを続ける。件数を減らすのは外部障害（AI利用制限・タイムアウト・本文不足・送信失敗）と、安全上限に達した場合だけ。
- **実装**:
  - `src/ai_client.py`: `SummaryQualityError` を追加し、品質ガード抵触とJSON破損・AI障害を区別。見出しの固有語と最低35文字も `validate_summary_payload(title=...)` で検証し、AI内の再生成ループで解消する。
  - `src/pipeline.py`: 修正要約が品質ガードに抵触しても `REVIEW_REJECTED` にせず再生成を続ける。外部の `AIClientError` は従来どおり即フェイルクローズ。修正試行ごとに文構造のバリエーションを渡し、同じ失敗の固定化を防ぐ。
  - `config/config.example.yaml`: `max_summary_attempts` / `max_revision_attempts` を追加（既定60）。無限ループを避ける安全上限であり、通常運用では到達しないことを想定。
  - `config/prompts/shorten.txt`: 再生成の文構造バリエーションは `{{FAILURE_INSTRUCTION}}` に載せ、長文追記によるコンテキスト超過を回避。
  - `config/prompts/{summarize,revise,shorten}.txt`: 55文字内で初見用語の説明が入らない場合は略語を書かず、記事本文の意味だけを使う逃げ道を追加（略語を使うなら説明必須は維持）。
  - `src/ai_client.py`: 初見用語の失敗時は検出した用語を明示し、略語を書かず記事本文の意味だけを使う再生成指示を渡す。
  - `src/pipeline.py`: プロンプト強化分を吸収するため、AI渡し本文を450→350文字へ短縮（要約プロンプト約2986文字、リード文と用語説明は維持）。
  - `src/ai_client.py`: 1〜3文字の超過は安全な「しました」→「した」（発生しました→発生した）だけへ機械的に短縮する。「ました」→「した」は「発生しした」等の不自然な述語を生むため禁止。事実は追加せず、置換後も体言止め・不自然な述語・4W1H・初見用語などの全ガードを再検査する。
- **検証**: `tests/test_pipeline.py` に「品質不合格の修正が続いた後に承認されること」「AI障害は即停止すること」を追加。全102件PASS。
- **実試験**: 対象記事で3回の要約再生成後にレビュー `PASS`。承認要約 `藤井総務相がNTTドコモの「5G専用設備」による通信障害の原因究明を求めました。`（40文字・1行）を要約のみで実送信し、`NOTIFIED` を確認。
