# ochinana-ads-mcp

Google 広告の運用を手伝う MCP サーバー（Claude などの AI から呼ぶ道具の集まり）です。手元のコンピューターで動かします。

- **広告アカウントは読むだけです。** アカウントを変える呼び出し（入札・予算・広告の変更など）は、コードのどこにもありません。
- 読んでよいアカウントは、自分で決めた許可リストのものだけです。リストに無い ID は、API を呼ぶ前に断ります。
- 鍵（API の認証情報）は自分で作り、手元のファイルに置きます。道具にはファイルの場所だけを教えます。道具は鍵の中身を読まず、出力もしません。
- 計算は道具がします。AI に暗算させず、根拠の数字と出どころを一緒に返します。

Google の公式の道具ではありません。

知識ベースの中身を図と表で読める運用サイト: https://nanako-dev.github.io/ads-analytics-map/

## できること（道具 51 個）

| まとまり | 道具 |
|---|---|
| 読む（Google 広告） | `list_accounts`・`get_performance`・`get_search_terms`・`get_keywords`・`get_ads_and_assets`・`get_generated_text`・`get_change_history` |
| 点検する | `run_checks`（入札と成果の設定・広告の有効性・表示オプション・キーワードと広告文のずれ・重複・検索語句・地域・LP の速度・広告ランクと予算で失った表示）、`audit_landing_page` |
| 変化を見る | `measure_effect`（変更の前後の差を偶然の範囲と並べる。因果は判定しない）、`get_anomalies`（いつもの水準からの外れ） |
| 見込む | `forecast_volume`・`forecast_vs_actual` |
| レポート | `report_numbers`（数字の束: 合計・増減・要因の分解・内訳・目標と計画・成果の遅れ）、`report_number_check`（文章の数字を束と突き合わせる）、`report_weekly_self`（週次の材料と骨組み）、`report_to_html`、`report_sheet_plan`・`report_sheet_check`（スプレッドシートに書く中身と照らし。シートへは書かない） |
| 計算する（外へ送らない） | `ad_text_check`・`url_builder`・`unit_economics`・`cpa_roas_simulate`・`media_plan_simulate`（月 × 媒体の予算の配分）・`fit_response_curve`（費用と成果の点に近似曲線）・`budget_pace`・`image_spec_check`・`significance`・`data_needed`・`compare_variants`・`list_compare`・`feed_check`・`video_spec_check`・`video_storyboard_check` |
| 判断の記録 | `log_decision`・`list_decisions`（手元のファイルにだけ書く） |
| 運用の原則 | `get_principles`（`principles.md` を読む） |
| 知識ベース | `kb_*`・`kb_video_guide`（同梱の `kb/` を引く。下の「知識ベース」を参照） |
| 知見カード | `get_new_intel`・`search_cards`・`match_card_to_account`（カードは同梱していない。`ADS_INTEL_DIR` で指す） |

## 外へ送るもの

- Google 広告 API へ、読み取りの問い合わせ（GAQL）を送ります。変更する呼び出しはありません。
- `audit_landing_page` と `run_checks` の LP の点検は、LP の URL を PageSpeed Insights（Google の公開の計測）に送ります。送るのは URL だけです。
- `forecast_volume` は、キーワードの語・地域・言語を Google 広告 API（キーワードプランナー）に送ります。API の利用権限が Basic 以上のときだけ動きます。
- そのほかの道具は、外へ何も送りません。

## 入れ方

前提: Python 3.12 以上と [uv](https://docs.astral.sh/uv/)。

```bash
git clone <このリポジトリ>
cd ochinana-ads-mcp
uv sync
uv run pytest -q
```


### 鍵（Google 広告 API）

1. Google 広告の API センターで開発者トークンを発行し、Google Cloud で OAuth の client（デスクトップ アプリ）を作ります。
2. `google-ads.yaml.example` を写して自分の値を入れ、**このフォルダの外**の手元の安全な場所に置きます。
3. 環境変数 `GOOGLE_ADS_CONFIGURATION_FILE_PATH` に、そのファイルの場所を入れます。

鍵の実物をリポジトリに入れたり、チャットに貼ったりしないでください。`.gitignore` で `*.yaml`・`*.json`・`.env` を外してあります。

### 設定（環境変数）

| 変数 | 中身 | 無いとき |
|---|---|---|
| `GOOGLE_ADS_CONFIGURATION_FILE_PATH` | 認証ファイルの場所 | Google 広告を読む道具が断る |
| `ADS_ALLOWED_CUSTOMER_IDS` | 読んでよいアカウントの ID（カンマ区切り、ハイフンなし 10 桁） | どのアカウントも断る |
| `ADS_FROZEN_CAMPAIGNS` | 検証中で変えないキャンペーン（JSON: `{"名前": ["最終日", "理由"]}`）。レポートの「次にやること」で、変える案を「様子を見る」に回す | 固定なし |
| `ADS_KB_DIR` | 知識ベース（Markdown のメモ）のフォルダ | 同梱の `kb/Google広告の基礎` を使う |
| `ADS_INTEL_DIR` | 知見カード（`cards/`）のフォルダ | カードの道具が断る |
| `ADS_BENCHMARK_FILE` | 業種の平均の JSON | 同梱の `kb/_ベンチマーク/benchmarks.json` を使う |
| `OCHINANA_ADS_OUT_DIR`・`OCHINANA_ADS_LOG_DIR` | 書き出しと判断の記録の置き場 | このフォルダの `out/`・`log/` |
| `OCHINANA_FFPROBE_BIN`・`OCHINANA_FFMPEG_BIN`・`OCHINANA_LIGHTHOUSE_BIN` | 手元の ffprobe・ffmpeg・Lighthouse の場所 | PATH から探す（無ければ代わりの方法か、断る） |

### Claude Code に登録する

```bash
claude mcp add ochinana-ads \
  -e GOOGLE_ADS_CONFIGURATION_FILE_PATH=/path/to/google-ads.yaml \
  -e ADS_ALLOWED_CUSTOMER_IDS=1234567890 \
  -- /path/to/ochinana-ads-mcp/.venv/bin/python -m ochinana_ads.server
```

`/path/to/…` と ID は自分のものに直してください。

## 知識ベース

`kb/` に、Google 広告の仕様と運用の考え方をまとめた Markdown のメモを入れています（`Google広告の基礎/` のほか、Meta・LINEヤフー・ChatGPT の広告のメモ、業種の平均）。`kb_*` の道具はここを引きます。ほかの計算の道具も、根拠の行をここから引いて一緒に返します。

- メモは公式のヘルプや資料で確かめた事実と、実務家の公開情報（仮説として扱う）を分け、印（`✅`・`△`・`❓`）と確認日を付けています。確認日から日がたった項目は、出典を取り直してください。
- 公開版では、一部の節と行を外しています（手元の検証の記録や、公開に向かない記述）。そのため、道具が根拠として引く行の一部は「知識ベースに見つからない」と返ります。そのときも計算は道具の中の値で行います。
- 自分のメモを使うときは `ADS_KB_DIR` でフォルダを指します。

## 運用の原則

`principles.md` に、この道具を使うときの原則を書いています。品質が先で入札は後、AI は提案までで変えるのは承認してから、確かめるためだけに広告費を使わない、個人情報を取らない成果地点、アカウントとデータを混ぜない・鍵を AI に渡さない、の 5 つです。道具はこの原則を形にしています（例: 点検の直し方の案は、入札・予算の案をいつも最後に置く）。

## テスト

```bash
uv run pytest -q
```

本物の API は呼びません（にせの応答で確かめます）。口座 ID はテスト用の作りものです。2 件は、取り込んだ計算の元のスクリプトが無い環境では飛ばします。

## ライセンス

ライセンスは付けていません。見るのは自由ですが、使う・写す・直す・配る許可は出していません。
