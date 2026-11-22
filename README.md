# ochinana-ads-mcp

Google 広告の運用を手伝う MCP サーバーです。MCP サーバーは、Claude などの AI から呼ぶ道具の集まりです。自分のコンピューターで動かし、**自分の Google 広告の認証を入れれば、誰のアカウントでも使えます**。

- **広告アカウントは読むだけです。** 入札・予算・広告を変える呼び出しは、コードのどこにもありません。
- **読むアカウントは、自分で決めた許可リストのものだけです。** リストに無い ID は、API を呼ぶ前に断ります。
- **鍵（認証の値）は自分の手元のファイルに置きます。** 道具にはファイルの場所だけを教えます。道具も AI も、鍵の中身を読んだり表示したりしません。
- **計算は道具がします。** AI に暗算させず、根拠の数字と出どころを一緒に返します。

Google の公式の道具ではありません。知識ベースの中身を図と表で読める運用サイト: https://nanako-dev.github.io/ads-analytics-map/

## 何ができるか

### 媒体ごと

| 媒体 | 実績を読む | レポート | 知識ベース |
|---|---|---|---|
| Google 広告 | **API で読む**（読むだけ） | API の数字から作る | あり（いちばん厚い。運用の型・ロジックツリー・Tips・用語集つき） |
| Meta 広告 | API ではつながっていない | 管理画面から書き出した CSV から作る | あり（`media="meta"`） |
| LINEヤフー広告 | 同上 | 同上 | あり（`media="lineyahoo"`） |
| ChatGPT の広告 | 同上 | 同上 | あり（`media="chatgpt"`） |

- CSV の列の名前は、各媒体の公式の列の説明に合わせています。実物の書き出しのファイルでは、まだ全部は試していません。
- 金額は**円のアカウント向け**です。円以外のアカウントは、実績を読む道具が断ります。

### 道具（53 個）

| まとまり | 道具 |
|---|---|
| つなぐ・確かめる | `check_connection`（認証ファイル → API → 許可リストの各アカウントの順に確かめ、つまずいた所と直し方を返す）、`list_accounts` |
| 読む（Google 広告） | `get_performance`・`get_search_terms`・`get_keywords`・`get_ads_and_assets`・`get_generated_text`・`get_change_history` |
| 点検する | `run_checks`（入札と成果の設定・広告の有効性・表示オプション・キーワードと広告文のずれ・重複・検索語句・地域・LP の速度・広告ランクと予算で失った表示）、`audit_landing_page` |
| 変化を見る | `measure_effect`（変更の前後の差を偶然の範囲と並べる。因果は判定しない）、`get_anomalies`（いつもの水準からの外れ） |
| 見込む | `forecast_volume`（キーワードプランナー。利用権限が Basic 以上のときだけ）・`forecast_vs_actual` |
| レポート | `report_numbers`（数字の束）、`report_numbers_csv`（媒体の CSV から同じ束を作る。API は呼ばない）、`report_number_check`（文章の数字を束と突き合わせる）、`report_weekly_self`（週次の材料と骨組み）、`report_to_html`、`report_sheet_plan`・`report_sheet_check`（スプレッドシートに書く中身と照らし。シートへは書かない） |
| 計算する（外へ送らない） | `ad_text_check`・`url_builder`・`unit_economics`・`cpa_roas_simulate`・`media_plan_simulate`・`fit_response_curve`・`budget_pace`・`image_spec_check`・`significance`・`data_needed`・`compare_variants`・`list_compare`・`feed_check`・`video_spec_check`・`video_storyboard_check` |
| 判断の記録 | `log_decision`・`list_decisions`（手元のファイルにだけ書く） |
| 運用の原則 | `get_principles`（`principles.md` を読む） |
| 知識ベース | `kb_overview`・`kb_search`・`kb_read`・`kb_rules`・`kb_check_claim`・`kb_quiz`（`media` で媒体を選ぶ）、`kb_kata`・`kb_logic_tree`・`kb_tips`・`kb_glossary`・`kb_video_guide`（Google 広告だけ） |
| 知見カード | `get_new_intel`・`search_cards`・`match_card_to_account`（カードは同梱していない。`ADS_INTEL_DIR` で指す） |

## 要るもの

- **コンピューター**: Mac・Windows・Linux。[Python](https://www.python.org/) 3.12 以上、[uv](https://docs.astral.sh/uv/)、git。
- **Google 広告のアカウント**: 読む権限のある Google のログイン。
- **Google Cloud のプロジェクト**: 自分で 1 つ作ります。
  - 請求先アカウントをつないだプロジェクトにします。Cloud の無料トライアルのままだと、利用権限の申請が断られます。
  - Google 広告 API の利用そのものに料金はかかりません。
- **パスキー**: 許可を出す Google のログインに、パスキーを登録しておきます（Google 広告 API の決まり）。
- **AI のアプリ**: Claude Code、Claude のデスクトップアプリ、または MCP（stdio）に対応したアプリ。

## 入れ方（上から順に）

### 1. 道具を手元に置く

```bash
git clone https://github.com/nanako-dev/ochinana-ads-mcp.git
cd ochinana-ads-mcp
uv sync
uv run pytest -q
```

テストが通れば、道具は動きます。テストは本物の API を呼びません。

### 2. Google Cloud で Google 広告 API を有効にする

1. [Google Cloud のコンソール](https://console.cloud.google.com/)でプロジェクトを作ります。
2. [Google Ads API のページ](https://console.cloud.google.com/apis/library/googleads.googleapis.com)で「有効にする」を押します。

この時点の利用権限は **Test** です。読めるのはテスト用のアカウントだけです。

### 3. 利用権限を Explorer に上げる

1. Cloud の [Google Ads API の概要](https://console.cloud.google.com/google/ads-apis/overview)を開きます。
2. 「Upgrade access level」から、Explorer を申請します。多くの場合は自動で審査され、数分で通ります。

| 利用権限 | 読めるアカウント | 1 日の上限（本番のアカウント） | キーワードプランナー（`forecast_volume`） |
|---|---|---|---|
| Test | テスト用だけ | — | 使えない |
| Explorer | 本番も | 2,880 操作 | **使えない** |
| Basic | 本番も | 15,000 操作 | 使える（ブランドの確認のあとに申請） |

- 利用権限は、OAuth クライアントを持つ Cloud のプロジェクトに付きます。
- 開発者トークンは 2026-09-09 に廃止されました。発行も、設定に書くことも要りません。

### 4. OAuth の同意画面とクライアントを作る

Cloud の「Google Auth Platform」で、次の 4 つを設定します。

1. **ブランディング**: アプリの名前とサポートのメールを入れます。
2. **対象**: 「外部」を選び、**「本番」に公開**します。
   - 「テスト中」のままだと、認証が **7 日で切れます**。
3. **データアクセス**: スコープに `https://www.googleapis.com/auth/adwords` を足します。
4. **クライアント**: 「作成」で、種類「**デスクトップ アプリ**」を作り、JSON をダウンロードします。

ダウンロードした JSON はこのフォルダに入れず、手元の安全な場所に置きます。

### 5. 認証ファイルを作る（自分の端末で動かす）

```bash
uv run ochinana-ads-connect token --client-secret ~/Downloads/client_secret_XXXX.json --out ~/.config/ochinana-ads/google-ads.yaml
```

ブラウザで Google のログインと許可の画面が開くので、許可します。

- 受け取った値は認証ファイルにだけ書き、画面には出しません。
- ファイルは、本人だけが読める権限（600）で作ります。
- この道具のフォルダの中には作れません。
- マネージャー（MCC）経由で読むときは、`--login-customer-id 1234567890`（マネージャーの 10 桁）を足します。
- **AI に動かさせず、自分の端末で動かしてください。**

<details>
<summary>別の取り方（gcloud・サービスアカウント）</summary>

**gcloud で取る**（Google の推奨の 1 つ。[gcloud CLI](https://cloud.google.com/sdk/docs/install) が要る）:

```bash
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform --client-id-file=~/Downloads/client_secret_XXXX.json
```

認証ファイルには、次の 2 行を書きます。

```yaml
use_application_default_credentials: true
use_proto_plus: true
```

**サービスアカウント**（Google が勧める方法）:

1. Cloud でサービスアカウントと JSON の鍵を作ります。
2. Google 広告の管理画面で、そのメールをユーザーに足します（管理 → アクセスとセキュリティ。権限は「メールのみ」以外）。
3. 認証ファイルに、次の 2 行を書きます。

```yaml
json_key_file_path: "/安全な場所/service-account.json"
use_proto_plus: true
```

見本は `google-ads.yaml.example` にあります。

</details>

### 6. 読むアカウントを選ぶ（自分の端末で動かす）

```bash
GOOGLE_ADS_CONFIGURATION_FILE_PATH=~/.config/ochinana-ads/google-ads.yaml uv run ochinana-ads-connect check
```

このログインで読めるアカウントが、マネージャーの配下も含めて一覧になります。AI に読ませるアカウントの番号を入れると、許可リストの 1 行が出ます。

```text
ADS_ALLOWED_CUSTOMER_IDS=1234567890
```

この一覧には、勤務先や取引先のアカウントが混じることがあります。そのため、端末にだけ出し、AI には渡しません。一覧を AI に貼らないでください。

### 7. AI のアプリに登録する

`/path/to/…` と ID は、自分のものに直してください。

**Claude Code**

```bash
claude mcp add ochinana-ads -e GOOGLE_ADS_CONFIGURATION_FILE_PATH=/path/to/home/.config/ochinana-ads/google-ads.yaml -e ADS_ALLOWED_CUSTOMER_IDS=1234567890 -- /path/to/ochinana-ads-mcp/.venv/bin/ochinana-ads
```

**Claude のデスクトップアプリ**

設定ファイル `claude_desktop_config.json` の `mcpServers` に足します。

- 置き場所: Mac は `~/Library/Application Support/Claude/`、Windows は `%APPDATA%\Claude\`
- 足したら、アプリを終了して開き直します。

```json
{
  "mcpServers": {
    "ochinana-ads": {
      "command": "/path/to/ochinana-ads-mcp/.venv/bin/ochinana-ads",
      "env": {
        "GOOGLE_ADS_CONFIGURATION_FILE_PATH": "/path/to/home/.config/ochinana-ads/google-ads.yaml",
        "ADS_ALLOWED_CUSTOMER_IDS": "1234567890"
      }
    }
  }
}
```

- Windows では `command` を `C:\\path\\to\\ochinana-ads-mcp\\.venv\\Scripts\\ochinana-ads.exe` にします。
- パスは省略せず、ドライブ名や `/` から書きます。

**ほかの MCP のアプリ**

stdio のサーバーとして登録します。設定は次の 2 つです。

- コマンド: `/path/to/ochinana-ads-mcp/.venv/bin/ochinana-ads`（`python -m ochinana_ads.server` でも同じ）
- 環境変数: `GOOGLE_ADS_CONFIGURATION_FILE_PATH` と `ADS_ALLOWED_CUSTOMER_IDS`

### 8. 確かめる

AI に「**check_connection で確かめて**」と頼みます。次の 3 段を順に確かめ、つまずいた段と直し方を返します。

1. 認証ファイル（中身は開かず、有無・権限・置き場所だけ）
2. Google 広告 API
3. 許可リストの各アカウント

「つながった」と出れば終わりです。続けて「list_accounts で読めるアカウントを見せて」と頼むと、名前・通貨・タイムゾーンが出ます。

## つまずいたとき

| 出たこと | わけ | 直し方 |
|---|---|---|
| `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` | 利用権限が Test のまま | 入れ方 3 で Explorer を申請する |
| Explorer の申請が断られる | 無料トライアルのプロジェクトか、請求が止まっている | 請求先アカウントを有料にするか、請求を戻す |
| 1 週間ほどで `invalid_grant` などの認証の失敗 | 同意画面が「テスト中」のまま（7 日で切れる） | 「本番」にして、入れ方 5 を動かし直す |
| `USER_PERMISSION_DENIED` | このログインにアカウントの権限が無い。またはマネージャー経由なのに `login_customer_id` が無い | 認証ファイルに `login_customer_id`（マネージャーの 10 桁）を書く |
| 2026-09-09 より後に上げたのに、本番だけ `AUTHORIZATION_ERROR` | Google の既知の問題 | 公式の回避策は、新しい Cloud プロジェクトで Explorer を申請し直すこと |
| `forecast_volume` が動かない | キーワードプランナーは Basic から | ブランドの確認のあとに Basic を申請する。それまでは、画面で見た月間検索数を渡すと計算できる |
| `RESOURCE_EXHAUSTED` | 1 日の上限（Explorer は 2,880 操作） | 24 時間の幅で数えるので、時間をおく |
| `connect check` が「端末で直接動かす」で止まる | AI や別のプログラムから動かした | 自分の端末で直接動かす |
| 円以外のアカウントで断られる | 円のアカウント向けの作り | — |
| 「知識ベースのフォルダが見つからない」 | `kb/` が無いか、`ADS_KB_DIR` の場所が違う | 場所を確かめる |

道具の返すエラーには、分かるものに「直し方」を付けています。

## 設定（環境変数）

| 変数 | 中身 | 無いとき |
|---|---|---|
| `GOOGLE_ADS_CONFIGURATION_FILE_PATH` | 認証ファイルの場所 | Google 広告を読む道具が断る |
| `ADS_ALLOWED_CUSTOMER_IDS` | 読んでよいアカウントの ID（カンマ区切り、10 桁） | どのアカウントも断る |
| `ADS_FROZEN_CAMPAIGNS` | 検証中で変えないキャンペーン（JSON: `{"名前": ["最終日", "理由"]}`）。レポートの「次にやること」で、変える案を「様子を見る」に回す | 固定なし |
| `ADS_KB_DIR` | Google 広告の知識ベースのフォルダ | 同梱の `kb/Google広告の基礎` |
| `ADS_KB_DIR_META`・`ADS_KB_DIR_LINEYAHOO`・`ADS_KB_DIR_CHATGPT` | ほかの媒体の知識ベースのフォルダ | `ADS_KB_DIR` と同じ階層の `Meta広告の基礎` など |
| `ADS_INTEL_DIR` | 知見カード（`cards/`）のフォルダ | カードの道具が断る |
| `ADS_BENCHMARK_FILE` | 業種の平均の JSON | 同梱の `kb/_ベンチマーク/benchmarks.json` |
| `OCHINANA_ADS_OUT_DIR`・`OCHINANA_ADS_LOG_DIR` | 書き出しと判断の記録の置き場 | このフォルダの `out/`・`log/` |
| `OCHINANA_FFPROBE_BIN`・`OCHINANA_FFMPEG_BIN`・`OCHINANA_LIGHTHOUSE_BIN` | 手元の ffprobe・ffmpeg・Lighthouse の場所 | PATH から探す |

鍵の実物をリポジトリに入れたり、チャットに貼ったりしないでください。`.gitignore` で `*.yaml`・`*.json`・`.env` を外しています。

## 外へ送るもの

- Google 広告 API へ、読み取りの問い合わせ（GAQL）を送ります。変更する呼び出しはありません。
- `audit_landing_page` と `run_checks` の LP の点検は、LP の URL を PageSpeed Insights（Google の公開の計測）に送ります。送るのは URL だけです。
- `forecast_volume` は、キーワードの語・地域・言語を Google 広告 API（キーワードプランナー）に送ります。
- `ochinana-ads-connect token` は、許可を受けるために Google のログインの画面とやり取りします。
- そのほかの道具は、外へ何も送りません。

## 知識ベース

`kb/` に、広告の仕様と運用の考え方をまとめた Markdown のメモを入れています。`Google広告の基礎/`・`Meta広告の基礎/`・`LINEヤフー広告の基礎/`・`ChatGPT広告の基礎/` と、業種の平均です。

- `kb_*` の道具はここを引きます。ほかの媒体は `media`（`meta`・`lineyahoo`・`chatgpt`）を付けて引きます。
- 呼ぶたびにフォルダを読み直すので、メモ（章）を足すとすぐ引けます。
- メモは、公式のヘルプや資料で確かめた事実と、実務家の公開情報（仮説として扱う）を分けています。印（`✅`・`△`・`❓`）と確認日を付けています。確認日から日がたった項目は、出典を取り直してください。
- 公開版では、一部の節と行を外しています。そのため、道具が根拠として引く行の一部は「知識ベースに見つからない」と返ります。そのときも、計算は道具の中の値で行います。

## レポートのスプレッドシート版

`report_sheet_plan` は、Google スプレッドシートに送る要求（タブ・値と式・書式・グラフ）を作るだけです。シートへ書くのは、AI が別の道具（Google スプレッドシートの道具）で行います。

- タブの並びは次のとおりです。
  サマリー → 週別の推移 → 日別 → キャンペーン別 → 広告グループ別 → 広告別 → キーワード別 → 検索語句別 → 判断の記録 → 設定 → 日別データ → 日別データ_詳細 → データの出どころ
- 期間は「設定」タブの日付で決まります。ほかのタブの数字は「日別データ」を数える式です。
- `report_sheet_check` で、シートの表示を束と 1 ますずつ照らします。

## 日別データを毎日足すスクリプト（下書き）

`スクリプト/日別データを書き出す.js` は、Google 広告スクリプトの下書きです。

- キャンペーン別・日別の実績を読み、運用シートの「日別データ」タブに書きます。
- 広告アカウントを変える呼び出しはありません。
- `DRY_RUN` の既定は `true` です（書かずにログに出すだけ）。
- 入れ方は `スクリプト/入れ方.md` にあります。入れる・許可する・予約するのは、アカウントの持ち主本人です。

## 運用の原則

`principles.md` に、この道具を使うときの原則を書いています。

1. 品質が先で、入札は後
2. AI は提案までで、変えるのは承認してから
3. 確かめるためだけに広告費を使わない
4. 個人情報を取らない成果地点
5. アカウントとデータを混ぜない・鍵を AI に渡さない

## テスト

```bash
uv run pytest -q
```

- 本物の API は呼びません（にせの応答で確かめます）。
- 口座 ID はテスト用の作りものです。
- 2 件は、取り込んだ計算の元のスクリプトが無い環境では飛ばします。

## 公式の出どころ（2026-11-22 に確かめた）

- [Developer token](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)（廃止と、利用権限の移り方、既知の問題）
- [Access levels and permissible use](https://developers.google.com/google-ads/api/docs/api-policy/access-levels)（利用権限ごとの上限と、使えない機能）
- [Python のクライアントの認証](https://developers.google.com/google-ads/api/docs/client-libs/python/authentication)・[サービスアカウント](https://developers.google.com/google-ads/api/docs/oauth/service-accounts)
- [Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)（テスト中の 7 日の期限）
- [Connect to local MCP servers](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers)（デスクトップアプリの設定ファイル）

## ライセンス

ライセンスは付けていません。見るのは自由ですが、使う・写す・直す・配る許可は出していません。
