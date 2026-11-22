"""OCHINANA Ads MCP（段 1〜段 3.6、知識ベースと小さな道具）。手元で stdio で動かす。広告アカウントは読むだけ。

起動: .venv/bin/python -m ochinana_ads.server
（mcp 2.x では FastMCP の名前が MCPServer に変わった。書き方は同じ）
"""
from __future__ import annotations

import functools

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import abcd, calc, curvefit, decisions, feed, kata, kb, mediaplan, quiz, simulate, specs, stats, tools, urltool, video
from .client import SetupError, explain_api_error, explain_auth_error
from .config import DEFAULT_TOP_N, VERSION

server = MCPServer(
    name="ochinana-ads",
    version=VERSION,
    instructions=(
        "OCHINANA の広告運用セット。Google 広告の許可したアカウント（環境変数 ADS_ALLOWED_CUSTOMER_IDS）を読むだけで、変更はできない。ID は list_accounts で確かめる。金額は円。つながらない・入れたばかりのときは check_connection。\n"
        "1. 広告運用の質問には、答える前に必ず知識ベースを引く。会話の最初の 1 回は kb_overview と kb_read(file='90')"
        "（核となる理解。無ければ kb_read(file='型00')）を読む。\n"
        "2. 問いの型ごとの引き方: できる／できない・上限 → kb_rules。なぜ → kb_read（00 と該当メモ）。"
        "指標が動いた → kb_logic_tree。次に何をするか → kb_kata（kind='状態' で決定木の問いに答えて状態を決め、"
        "state='状N' でその状態の規則を引く）。やり方の助言 → kb_tips（仮説と断る）。主張の真偽 → kb_check_claim。"
        "用語 → kb_glossary。語で広く → kb_search。Meta・LINEヤフー・ChatGPT の広告 → kb_* に media（meta／lineyahoo／chatgpt）を付ける（その媒体に無い種類のメモは、無いと返る）（この 3 媒体は API でつながっていない。数字は管理画面の CSV から report_numbers_csv）。計算 → 小さな道具（ad_text_check・url_builder・unit_economics・"
        "budget_pace・cpa_roas_simulate・media_plan_simulate・fit_response_curve・image_spec_check・significance・data_needed・compare_variants・list_compare・feed_check）で、暗算しない。"
        "アカウントの診断 → run_checks。誰がいつ何を変えたか → get_change_history。変更の前後 → measure_effect（前後の差まで。因果と言わない）。"
        "いつもの水準から外れたもの → get_anomalies。"
        "レポート: 週次（自分用）は report_weekly_self で材料と骨組みを取り、それ以外は先に report_numbers（媒体の CSV なら report_numbers_csv）で数字の束を作り、文章はその数字だけで書き、表は束の「表」をそのまま貼り、"
        "最後に report_number_check で文章の数字を突き合わせて「直すもの」を 0 件にする。"
        "動画: 作り方のノウハウ（ABCD などの中身と、企画〜入稿・検証の工程）→ kb_video_guide（工程は phase で絞る）。台本・絵コンテの見直し → video_storyboard_check"
        "（判定せず問いを返す）。動画ファイルの尺・比率・解像度 → video_spec_check。動画広告の文字数 → ad_text_check（動画の種類）。\n"
        "3. 印の扱い: ❓ と、知識ベースに無いことは「未確認」と言う。△ は両方の記述を伝える。"
        "〔推論〕のしきい値は「公式の数字ではない目安」と断る。Tips は仮説。\n"
        "4. 入札や予算を変える提案の前に、運用の型の順序（計測 → 成果の定義 → 絞る → 素材 → 構成 → 入札・予算 → 広げる）"
        "で上流が片づいているかと、時間の法則（評価の期間と件数。kb_kata で 閾02・閾04、kb_read(file='型04')）を確かめる。"
        "get_principles も読み、変更は運用者の承認を取る。\n"
        "5. 確認日から 30 日を過ぎた項目は、出典を取り直すよう促す。\n"
        "ファイルの指定: 直下のメモは番号（12）、運用の型/ のファイルは 型NN（型12、付録は 型90〜型94）。"
        "1 本に決まらないときは候補が返るので、その名前で指定し直す。"
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def _guard(fn):
    """想定できる失敗は、理由が分かる短い日本語にして返す（長いトレースや認証情報を出さない）。

    入力の誤り・設定の不足・API が断った場合がこれにあたる。それ以外（コードの不具合）は
    そのまま上げ、中身は利用側に出さずサーバーのログにだけ残す。
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, SetupError) as ex:
            raise ToolError(str(ex)) from None
        except Exception as ex:
            if getattr(ex, "failure", None) is not None:  # Google 広告 API が返したエラー
                raise ToolError(explain_api_error(ex)) from None
            if type(ex).__module__.startswith("google.auth"):
                raise ToolError(explain_auth_error(ex)) from None
            raise

    return wrapper


@server.tool(title="接続を確かめる", annotations=READ_ONLY)
@_guard
def check_connection() -> dict:
    """入れたあと・つながらないときに最初に呼ぶ。認証ファイル → Google 広告 API → 許可リストの各アカウント、の順に確かめ、
    つまずいた段と直し方を返す。

    認証ファイルは中身を開かない（有無・権限・置き場所だけ）。許可リストに無いアカウントの ID と名前は返さない
    （直接読める件数だけ）。読むアカウントを選ぶのは、本人が端末で `uv run ochinana-ads-connect check` を動かして行う。
    """
    return tools.check_connection()


@server.tool(title="使えるアカウントの一覧", annotations=READ_ONLY)
@_guard
def list_accounts() -> dict:
    """読んでよい Google 広告アカウントの ID・名前・通貨・タイムゾーンと、国・言語の既定値を返す。

    ほかの道具に渡す customer_id はここで確かめる。国と言語の既定値は、
    キャンペーンの配信設定でいちばん多い指定から取り、根拠も一緒に返す。
    """
    return tools.list_accounts()


@server.tool(title="実績を読む", annotations=READ_ONLY)
@_guard
def get_performance(customer_id: str, start: str, end: str, level: str = "campaign",
                    top_n: int = DEFAULT_TOP_N) -> dict:
    """期間の実績（表示回数・クリック数・費用・コンバージョン）を、指定した単位で返す。

    customer_id: 10 桁のアカウント ID。
    start / end: YYYY-MM-DD（アカウントのタイムゾーンの日付。両端を含む）。
    level: campaign（キャンペーン）/ ad_group（広告グループ）/ keyword（キーワード）。
    top_n: 表示する行数（既定 20、最大 100）。合計は全件で計算する。
    費用は円で返す。Google 広告 API の micros（100 万分の 1 円）は外に出さない。
    クリック率や単価は計算済みの値を返す。期間に実績が無い行は省く。
    """
    return tools.get_performance(customer_id, start, end, level, top_n)


@server.tool(title="検索語句を読む", annotations=READ_ONLY)
@_guard
def get_search_terms(customer_id: str, start: str, end: str, top_n: int = DEFAULT_TOP_N,
                     sort_by: str = "費用") -> dict:
    """実際に検索された語句と、その表示回数・クリック数・費用（円）を返す。

    返すのは上位 top_n 件（既定 20）と全件の合計。全件は CSV に保存して保存先だけ返す。
    sort_by: 費用 / クリック / 表示。
    検索数の少ない語句は Google 側で伏せられる。P-MAX の語句は含まない。
    ここに出るのは検索された語句で、登録してあるキーワードや除外キーワードは get_keywords で読む。
    """
    return tools.get_search_terms(customer_id, start, end, top_n, sort_by)


@server.tool(title="キーワードと除外キーワードを読む", annotations=READ_ONLY)
@_guard
def get_keywords(customer_id: str, top_n: int = DEFAULT_TOP_N) -> dict:
    """登録してあるキーワードと一致方式・状態を返す。実績の数字は含まない（get_performance で読む）。

    入札するキーワードは「キーワード」（negative: false）、除外キーワードは「除外キーワード」
    （negative: true）と、欄を分けて返す。除外キーワードは、広告グループ・キャンペーン・
    共有の除外リストの 3 か所から集める。各欄は top_n 件（既定 20）まで。全件は CSV に保存する。
    """
    return tools.get_keywords(customer_id, top_n)


@server.tool(title="運用の原則を読む", annotations=READ_ONLY)
@_guard
def get_principles() -> str:
    """運用者が決めた原則（順番と禁止事項）の全文を返す。

    媒体の推奨や集めた知見より上に置く文書。入札・予算・広告文・除外などの提案を
    組み立てる前に読む。
    """
    return tools.get_principles()


# ---- 段 2 ----

LOCAL_CALC = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


@server.tool(title="広告文と表示オプションを読む", annotations=READ_ONLY)
@_guard
def get_ads_and_assets(customer_id: str, only_enabled: bool = True, include_text: bool = True) -> dict:
    """広告グループごとの広告文（見出し・説明文の本数と文）、広告の有効性、審査の状態と、表示オプションの件数・状態を返す。

    表示オプション（サイトリンク・コールアウト・構造化スニペット・画像ほか）は、付け先の階層
    （アカウント・キャンペーン・広告グループ）ごとに数える。主な 4 種類は 0 件でも必ず出す。
    only_enabled: true なら有効なキャンペーン・広告グループ・広告だけ（既定）。
    include_text: false にすると本数だけ返す。
    """
    return tools.get_ads_and_assets(customer_id, only_enabled, include_text)


@server.tool(title="Google が自動で作った文を読む", annotations=READ_ONLY)
@_guard
def get_generated_text(customer_id: str, start: str, end: str, top_n: int = DEFAULT_TOP_N) -> dict:
    """Google が自動で作った見出し・説明文と、その着地 URL を期間の実績つきで返す。

    返すのは 3 つ: 自動で作られた文、AI Max が出した見出しと着地 URL の組み合わせ、
    着地 URL と選ばれ方（広告主が指定／Google が自動で選択）。無いものは「まだ無い」と返す。
    start / end: YYYY-MM-DD。金額は円。
    """
    return tools.get_generated_text(customer_id, start, end, top_n)


@server.tool(title="点検を実行する", annotations=READ_ONLY)
@_guard
def run_checks(customer_id: str, start: str, end: str, include_lp: bool = True) -> dict:
    """有効なキャンペーンに点検 10 個（C01〜C10）を実行し、所見の一覧を返す。アカウントは読むだけ。

    所見には 重さ（大・中・小）、根拠の数字、直し方の案が付く。直し方の案は機械が出したもので、
    どれも「AI の提案（未承認）」。入札・予算に触れる案は最後に置いてある。
    材料が無くて決められない点検は「判定できない」と理由つきで返す。
    start / end: 実績と検索語句を見る期間（YYYY-MM-DD）。
    include_lp: true なら広告の最終 URL のスマホ表示速度も測る（1 本あたり数十秒。最大 3 本）。
    """
    return tools.run_checks(customer_id, start, end, include_lp)


@server.tool(title="LP を監査する", annotations=READ_ONLY)
@_guard
def audit_landing_page(url: str) -> dict:
    """LP 1 本の、スマホの表示速度と、ページの基本の要素・タグの有無を返す。

    表示速度は PageSpeed Insights（Google の公開の計測。鍵なし）で測り、総合点・最初の表示・
    メイン画像の表示・描画を止めている資源の上位だけを返す。外へ送るのは URL だけ。
    ページは 1 回取得して、title・meta description・h1・canonical、Google タグの有無を見る。
    生の結果は保存して、保存先だけ返す。公開されているページの URL を渡す。数十秒かかる。
    """
    return tools.audit_landing_page(url)


@server.tool(title="必要なデータ量を計算する", annotations=LOCAL_CALC)
@_guard
def data_needed(baseline_rate: float, target_rate: float, daily_volume: float,
                power: float = 0.8, alpha: float = 0.05) -> dict:
    """2 案の率の差を見つけるのに必要な量と日数を計算し、テストするかどうかの扱いを返す。

    baseline_rate: 今の率（0〜1。2% なら 0.02）。target_rate: 見つけたい率（同じ形）。
    daily_volume: 1 日の量（率の分母になる数。2 案の合計）。
    扱いは 3 つ: 14 日以内に集まる＝テストする／60 日以内＝手前の指標か途中経過で見る／それ以上＝テストしない。
    式と前提も返す。数字はこの道具の結果を使い、暗算で出さない。
    """
    return stats.data_needed(baseline_rate, target_rate, daily_volume, power, alpha)


@server.tool(title="2 案を比べる", annotations=LOCAL_CALC)
@_guard
def compare_variants(a_success: int, a_total: int, b_success: int, b_total: int) -> dict:
    """2 案それぞれの率と、B が A を上回る確率、止めてよいかの目安を返す。

    success は成果の数、total は全体の数（表示・クリック・訪問など）。
    成果か非成果が 5 件以下の案があるときは、確率が当てにならない旨を返し、止める判断には使わせない。
    """
    return stats.compare_variants(a_success, a_total, b_success, b_total)


@server.tool(title="判断を記録する", annotations=LOCAL_WRITE)
@_guard
def log_decision(customer_id: str, decision: str, reason: str, decided_by: str,
                 check_ids: list[str] | None = None, card_ids: list[str] | None = None, target: str = "") -> dict:
    """判断を 1 件、手元のファイル（log/decisions.jsonl）に足す。広告アカウントには何も書かない。

    decision: 何を判断したか。reason: なぜ。target: 対象（キャンペーン名など。省略可）。
    decided_by: 「AI の提案」「運用者の承認」「運用者の却下」のどれか。
    「運用者の承認」「運用者の却下」は、運用者がこの会話で実際にそう言ったときだけ使う。
    check_ids: 根拠にした点検 ID（例: ["C06"]）。card_ids: 根拠にした知見カードの ID。
    """
    return decisions.log_decision(customer_id, decision, reason, decided_by, check_ids, card_ids, target)


@server.tool(title="判断の記録を読む", annotations=LOCAL_CALC)
@_guard
def list_decisions(customer_id: str = "", limit: int = 20) -> dict:
    """記録した判断を新しい順に返す。customer_id を空にすると全アカウント分。limit は既定 20（最大 100）。"""
    return decisions.list_decisions(customer_id or None, limit)


# ---- 段 3 ----

READ_AND_LOG = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


@server.tool(title="配信量を見込む", annotations=READ_AND_LOG)
@_guard
def forecast_volume(customer_id: str, keywords: list[str], daily_budget_yen: float | None = None,
                    monthly_searches: dict[str, float] | None = None, history_start: str = "",
                    history_end: str = "", record: bool = True) -> dict:
    """キーワードごとに、月の表示・クリック・費用の見込みを幅つき（低・中・高）で返す。広告アカウントは読むだけ。

    式は「月間検索数 × 表示される割合 × クリック率 × クリック単価」。月間検索数はキーワードプランナー
    （読み取り。キーワードプランは作らない）から、割合・率・単価は自分の実績から取る。
    実績が少ないキーワードはアカウント全体の値で代用し、そう明記する。地域と言語はアカウントの既定値を使う。
    keywords: キーワードの並び（50 個まで）。daily_budget_yen: 1 日の予算（円）。渡すと費用をそこで頭打ちにする。
    monthly_searches: キーワードプランナーが使えないときに、画面で見た月間検索数を {キーワード: 数} で渡す。
    history_start / history_end: 割合・率・単価を取る期間（省くと昨日までの 180 日）。
    record: true なら見込みを手元の log/forecasts.jsonl に残す（あとで forecast_vs_actual で実績と比べる）。
    """
    return tools.forecast_volume(customer_id, keywords, daily_budget_yen, monthly_searches,
                                 history_start, history_end, record)


@server.tool(title="見込みと実績を比べる", annotations=READ_ONLY)
@_guard
def forecast_vs_actual(customer_id: str, start: str = "", end: str = "", forecast_id: str = "") -> dict:
    """記録してある見込みと、期間の実績を並べて差を返す。

    start / end（YYYY-MM-DD）を渡すと、月の見込みをその日数に直して実績と比べ、幅の中に入ったかを返す。
    forecast_id を省くと、いちばん新しい見込みを使う。start / end を省くと、見込みの記録の一覧を返す。
    """
    return tools.forecast_vs_actual(customer_id, start, end, forecast_id)


@server.tool(title="新しく入った知見を読む", annotations=LOCAL_CALC)
@_guard
def get_new_intel(since: str = "", top_n: int = DEFAULT_TOP_N) -> dict:
    """その日以降に入った知見カードと、扱いが未決のカードの件数・一覧（題名、段、日付、期限）を返す。

    since: YYYY-MM-DD（省くと 7 日前から）。手元の知見カードを読むだけで、カードは書き換えない。
    カードの要点は AI の下書き。段 B は仮説。扱いを決めるのは運用者。
    """
    return tools.get_new_intel(since, top_n)


@server.tool(title="知見カードを探す", annotations=LOCAL_CALC)
@_guard
def search_cards(query: str = "", status: str = "", tier: str = "", top_n: int = DEFAULT_TOP_N) -> dict:
    """知見カードを、語・扱い・段で探して要点を返す（何が変わったか、段、信頼度、扱い、期限、出どころの URL）。

    query: 空白で区切った語（すべて含むもの）。空なら全部。
    status: 未決 / 無視 / 見張る / 確かめる / 基準に反映する。tier: A / A' / B / C。
    """
    return tools.search_cards(query, status, tier, top_n)


@server.tool(title="知見カードをアカウントに照らす", annotations=READ_ONLY)
@_guard
def match_card_to_account(card_id: str, customer_id: str) -> dict:
    """知見カード 1 枚の「関係する設定」と、アカウントのいまの状態を並べ、当てはまるかを返す。アカウントは読むだけ。

    見るのは道具で読める範囲: キャンペーンの種類、入札戦略、AI Max の有無、言語の指定、直近 30 日の成果の件数、
    この道具が使っている API の版。判定は「当てはまる／当てはまらない／判断できない」。
    機械で照らせない語は判定に入れず、そのまま並べる。カードが AI の下書きで人の確認がまだのとき、
    信頼度が仮説のときは、その旨が「必ず伝えること」に入る。
    """
    return tools.match_card_to_account(card_id, customer_id)


# ---- 段 3.6: 変更の履歴・変更の前後・いつもの水準からの外れ（読むだけ） ----


@server.tool(title="変更の履歴を読む", annotations=READ_ONLY)
@_guard
def get_change_history(customer_id: str, start: str = "", end: str = "", top_n: int = DEFAULT_TOP_N) -> dict:
    """いつ・どの経路で・何を変えたか（変更の履歴）を新しい順に返す。アカウントは読むだけ。

    1 件ごとに、日時、種類（キャンペーン・予算・広告グループの条件など）、操作（作成・更新・削除）、
    経路（管理画面・自動ルール・スクリプト・最適化案の自動適用・API など）、キャンペーン名、
    変わった項目の前と後（金額は円）を返す。変更した人のメールアドレスは読まない。
    start / end: YYYY-MM-DD。API の決まりで過去 30 日までしか読めない（省くと過去 30 日）。
    それより前は管理画面の［変更履歴］で見る。Google 広告エディタの変更は API に返らない。
    全件は CSV に保存して保存先だけ返す。
    """
    return tools.get_change_history(customer_id, start, end, top_n)


@server.tool(title="変更の前後を比べる", annotations=READ_ONLY)
@_guard
def measure_effect(customer_id: str, change_date: str, days_before: int = 14, days_after: int = 14,
                   level: str = "campaign", campaign_id: str = "", lag_days: int = 7, skip_days: int = 0) -> dict:
    """変更日の前と後の実績（表示・クリック・費用・コンバージョン・クリック率・クリック単価・コンバージョン率・
    コンバージョン単価）を並べ、差が偶然の範囲を超えるかを返す。因果は判定しない（「前後の差」まで）。

    change_date: 変更した日（YYYY-MM-DD）。その日は比べに入れない。
    days_before / days_after: 前と後の日数（既定 14。7 の倍数にすると曜日の構成がそろう）。
    level: campaign（キャンペーンごとと合計）/ ad_group / account（合計だけ）。campaign_id で 1 本に絞れる。
    lag_days: 成果の遅れとして、直近から成果の系の比べに入れない日数（既定 7。知識ベース 90 の 5-3 の仮置き）。
    skip_days: 変更直後に外す日数（入札・予算の変更なら学習の期間を外す）。
    件数が足りない指標は「判定できない」と返す。差が無いという意味ではない。
    """
    return tools.measure_effect(customer_id, change_date, days_before, days_after, level, campaign_id,
                                lag_days, skip_days)


@server.tool(title="いつもの水準から外れたものを探す", annotations=READ_ONLY)
@_guard
def get_anomalies(customer_id: str, end: str = "", lookback_days: int = 28, level: str = "campaign") -> dict:
    """見た日（既定は昨日）の数字を、前の lookback_days 日（既定 28）の水準と比べ、外れを所見として返す。朝の点検の材料。

    見るもの: 配信の停止（D1）、表示・クリックの下振れ、クリック率の下振れ、費用の使いすぎ（日予算の 1.6 倍超が
    2 日続く。D2）・使えなさ（D3）、成果の 0 件の連続と 3 倍以上（D4）。偶然の範囲はポアソン分布と 2 つの率の検定で出す。
    所見には 重さ（大・中・小）、根拠の数字、知識ベース 36 の「確かめること」が付き、どれも「AI の提案（未承認）」。
    いつもの水準が小さすぎて決められないものは「判定できない」と返す。level: campaign / account。
    """
    return tools.get_anomalies(customer_id, end, lookback_days, level)


@server.tool(title="レポートの数字の束を作る", annotations=READ_ONLY)
@_guard
def report_numbers(customer_id: str, start: str, end: str, compare: str = "previous", compare_start: str = "",
                   compare_end: str = "", level: str = "campaign", campaign_id: str = "",
                   target_cpa_yen: float = 0, target_roas: float = 0, plan_cost_yen: float = 0,
                   plan_conversions: float = 0, plan_start: str = "", plan_end: str = "", lag_days: int = 7,
                   max_rows: int = 20, include_daily: bool = True) -> dict:
    """レポートを書く前に必ず呼ぶ。期間と比較期間の数字を、決まった定義で計算して「束」にする（AI に計算させない）。

    返すもの: 合計（今・前・差・増減率。率の差は pt）、要因の分解（費用＝表示×クリック率×クリック単価、
    コンバージョン＝表示×クリック率×コンバージョン率、コンバージョン単価＝クリック単価÷コンバージョン率、
    ROAS＝1件の値×コンバージョン率÷クリック単価 を対数の差で分け、各要因の寄与の割合を出す）、内訳（キャンペーンか
    広告グループごと。変化への寄与つき）、目標・計画との差、成果の遅れの注意（直近 lag_days 日は未確定）、日別（7 日平均つき）、
    そのまま貼れる Markdown の表、束ID。
    start / end: YYYY-MM-DD（終了日は昨日まで）。compare: previous（直前の同じ日数）/ week（7 日前）/ yoy（前年同期）。
    compare_start / compare_end を渡すと、その日付で比べる。
    target_cpa_yen / target_roas（%）: 目標。plan_cost_yen / plan_conversions: 計画（plan_start〜plan_end の全体の値。
    省くと今の期間の値とみなす）。経過日数で按分して進み具合を出す。
    文章を書いたら report_number_check(text, 束ID) で、文章の数字が全部この束にあるかを確かめる。表は「表」をそのまま使う。
    """
    return tools.report_numbers(customer_id, start, end, compare, compare_start, compare_end, level, campaign_id,
                                target_cpa_yen, target_roas, plan_cost_yen, plan_conversions, plan_start, plan_end,
                                lag_days, max_rows, include_daily)


@server.tool(title="CSV からレポートの数字の束を作る", annotations=LOCAL_CALC)
@_guard
def report_numbers_csv(csv_path: str, start: str, end: str, compare: str = "previous", compare_start: str = "",
                       compare_end: str = "", media: str = "", columns: dict[str, str] | None = None,
                       target_cpa_yen: float = 0, target_roas: float = 0, lag_days: int = 7) -> dict:
    """媒体の管理画面から書き出した CSV（手元のファイル）から、report_numbers と同じ数字の束を作る。API は呼ばない。

    Google 広告・Meta・LINEヤフー・ChatGPT 広告など。日別の行が要る（日付の列が無い CSV は断る）。
    文字コード（UTF-8・Shift_JIS・UTF-16）、表題の行、合計の行、「¥」「,」「--」は道具が処理する。列の名前は日本語か英語で探す。
    csv_path: .csv か .tsv。start / end: YYYY-MM-DD。compare: previous / week / yoy、または compare_start / compare_end。
    今の期間と比較期間は、同じ CSV の中から日付で分ける（比較期間の行が無ければ「注意」に出す）。
    media: 「Google 広告」「Meta」「LINEヤフー」「ChatGPT 広告」など。省くと列の名前から推定する。
    columns: 列の名前が当たらないときの指定（例: {"cost": "ご利用金額", "date": "集計日"}）。欄は date, campaign, campaign_id,
    media, impressions, clicks, cost, conversions, value, currency。値を "" にすると、その欄の列を使わない。
    返す束の「CSV の読み方」に、どの列を何として読んだかが出る。文章を書く前に確かめる。
    文章を書いたら report_number_check(text, 束ID) で突き合わせる。
    """
    return tools.report_numbers_csv(csv_path, start, end, compare, compare_start, compare_end, media, columns,
                                    target_cpa_yen, target_roas, lag_days)


@server.tool(title="文章の数字を束と突き合わせる", annotations=LOCAL_CALC)
@_guard
def report_number_check(text: str, bundle_id: str, allow: list[float] | None = None) -> dict:
    """書き上がったレポートの文章（Markdown 可）の数字が、report_numbers の束にあるかを機械で確かめる。

    結果: 一致／束に無い（写し間違い・暗算・持ち込みの疑い）／指標が違う疑い（数字は束にあるが、直前に書いた指標の
    項目には無い）／符号が違う。書いた桁で丸めて照らす（「約 1.2 万円」は千の位で丸める）。
    日付・時刻・見出しや箇条の番号・ID・「3 つ」などの数えは見ない。
    allow: 束の外だが出どころのはっきりした数字（判断の記録の目標値など）。「直すもの」が 0 件になるまで直してから出す。
    """
    return tools.report_number_check(text, bundle_id, allow)


@server.tool(title="週次の運用レポート（自分用）の材料と骨組み", annotations=READ_ONLY)
@_guard
def report_weekly_self(customer_id: str, end: str = "", target_cpa_yen: float = 0, target_roas: float = 0,
                       plan_cost_yen: float = 0, plan_conversions: float = 0, plan_start: str = "",
                       plan_end: str = "", include_checks: bool = True, lag_days: int = 7) -> dict:
    """週次の運用レポート（自分用）を書くときに呼ぶ。昨日まで（end で指定可）の 7 日と、その前の 7 日を比べる。

    集めるもの（どれも読むだけ）: 数字の束（report_numbers）、外れ（get_anomalies）、期間中の変更（get_change_history）、
    点検の所見（run_checks。LP は測らない）、判断の記録（過去 28 日。判断の日の前後 7 日の差と、評価の時期）。
    返すもの: 型（節の順番・中身・長さ・決まり）、骨組み（表と機械で書ける行は埋めてあり、文の所は【書く: …】）、
    ネクストアクションの候補（打ち手の段の順。入札・予算は最後に「それでも不足なら」）、束ID。
    外れ・変更・判断の数字も束に足してあるので、書き終えたら report_number_check(text, 束ID) で全部を突き合わせる。
    target_cpa_yen / target_roas / plan_*: report_numbers と同じ（目標と計画にも比べる）。
    """
    return tools.report_weekly_self(customer_id, end, target_cpa_yen, target_roas, plan_cost_yen, plan_conversions,
                                    plan_start, plan_end, include_checks, lag_days)


@server.tool(title="レポートを手元の HTML のページにする", annotations=LOCAL_CALC)
@_guard
def report_to_html(md_path: str = "", text: str = "", bundle_id: str = "", allow: list[float] | None = None) -> dict:
    """書き上がったレポート（Markdown）を、見やすい HTML のページにして手元に保存する（公開しない）。

    md_path: レポートの .md（同じ場所に同じ名前の .html を書く）。または text に Markdown を渡す（out/ に書く）。
    bundle_id: 省くと文章の中の束ID を使う。図（主な数字の欄・日別の費用とコンバージョン・キャンペーンの費用・打ち手の段）は束から描く。
    作る前に report_number_check を通し、その結果も返す（合わない数字があってもページは作るので、直してから作り直す）。
    """
    return tools.report_to_html(md_path, text, bundle_id, allow)


@server.tool(title="レポートをスプレッドシートに書く中身を作る", annotations=READ_ONLY)
@_guard
def report_sheet_plan(bundle_id: str, md_path: str = "", tab: str = "", levels: bool = True, weeks: int = 8,
                      refresh: bool = False, replace_sheet_ids: str = "", replace_named_range_ids: str = "") -> dict:
    """レポートを Google スプレッドシートに書くための値・式・書式・グラフを、束から作る（ads-mcp はシートに書かない）。

    書くのは Claude が Google スプレッドシートの道具で、運用者の Drive のシートに。
    タブの並び（広い順に絞る）: サマリー（数字のカード 8 つと前週比の色、日別の費用〔棒〕とコンバージョン〔線〕、今週のまとめ、
    次にやること）→ 週別の推移（直近 weeks 週、指標ごとに前週比の行、今週の列は赤い枠）→ 日別（全部の指標、土日・暫定の日の色、
    CV・CVR・CPA の棒グラフ）→ キャンペーン別・広告グループ別・広告別・キーワード別・検索語句別（1 対象 1 行で
    今週｜前週｜前週比 の列を横に並べ、見出しで並べ替え・絞り込み。今週の費用の大きい順に 30 件）→ 判断の記録 → 設定（期間の入力）
    → 日別データ → 日別データ_詳細 → データの出どころ。
    levels=True のときは、階層ごとの日別と、週別の推移のためのキャンペーンの日別を Google 広告から読む（読むだけ。一度読んだ行は
    束のファイルに残して使い回す。refresh=True で読み直す）。元のデータだけを値で入れ、ほかは全部 SUMIFS などの式。
    作り直すときは replace_sheet_ids（消すタブの sheetId、カンマ区切り）と replace_named_range_ids（消す名前付き範囲の id）を渡す。
    tab は使わない（前の版との互換のため）。
    「書き方」の順（先に送る要求 → 書く値 → あとで送る要求 → get_values → report_sheet_check）で使う。
    """
    return tools.report_sheet_plan(bundle_id, md_path, tab, levels, weeks, refresh, replace_sheet_ids, replace_named_range_ids)


@server.tool(title="スプレッドシートの合計を束と照らす", annotations=LOCAL_CALC)
@_guard
def report_sheet_check(bundle_id: str, values: list[list[str]]) -> dict:
    """シートに書いたあと、get_values で読んだ表（サマリーのカードと元の数字・週別の推移・日別・階層のタブ）を、束と保存した行に
    1 ますずつ照らす。表は見出しの行で見分けるので、確かめる範囲は見出しの行から読んで渡す。"""
    return tools.report_sheet_check(bundle_id, values)


# ---- 段 3.5: 知識ベースを引く道具（読むだけ。呼ぶたびにファイルを読み直す） ----

@server.tool(title="知識ベースの一覧", annotations=LOCAL_CALC)
@_guard
def kb_overview(kind: str = "", max_chars: int = 30000, media: str = "") -> dict:
    """知識ベースに何が入っているかの一覧を返す。会話の最初に 1 回呼ぶ。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    先頭の「最初に読むもの」に 90（核となる理解。あれば）と 運用の型/00（全体の地図）を置く。
    ファイルごとに 番号（kb_read に渡す名前）、種類、題名、一言、字数、確認日。
    種類: 核となる理解／運用の型（体系）／ルール統合表／知識メモ／運用の型（28・35〜37）／ロジックツリー／Tips／裏取りの記録／理解度テスト。
    kind で種類を絞れる。呼ぶたびにフォルダを読み直すので、足されたメモもすぐ出る。
    """
    with kb.use_media(media):
        return kb.overview(kind, max_chars)


@server.tool(title="知識ベースを語で探す", annotations=LOCAL_CALC)
@_guard
def kb_search(query: str, kind: str = "", file: str = "", max_results: int = 10, max_chars: int = 6000,
              media: str = "") -> dict:
    """知識ベース全体を語で探し、当たった所（ファイル・節・行・確認日・印・根拠の種類）を返す。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    query: 空白で区切った語。すべてを含むものを探す（部分一致。全角半角・大文字小文字・空白の違いは無視）。
    並び: 見出しの一致が先。その次は 核となる理解 → 運用の型（体系） → ルール統合表 → 知識メモ → ロジックツリー → Tips
    → 裏取りの記録 → 理解度テスト。表は 1 行を 1 件として返す。
    kind: 上の種類で絞る（「体系」「型」は 運用の型（体系））。
    file: 直下のメモは番号（例 15、12）、運用の型/ は 型NN（例 型08）、tips・ツリーは T1・L2、名前の一部でもよい。
    番号が重なる（94 など）ときは当たった全部から探し、注記で知らせる。
    max_results: 返す件数（既定 10、最大 50）。max_chars: 返答の文字数の上限（既定 6,000）。
    """
    with kb.use_media(media):
        return kb.search(query, kind, file, max_results, max_chars)


@server.tool(title="知識ベースの節を読む", annotations=LOCAL_CALC)
@_guard
def kb_read(file: str, section: str = "", toc_only: bool = False, max_chars: int = 8000, offset: int = 0,
            media: str = "") -> dict:
    """ファイルと節（見出し）を指定して、本文を Markdown のまま返す。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    file: 直下のメモは番号（例 00、15、90）、運用の型/ のファイルは 型NN（例 型04、型08、付録は 型91）、
    tips・ツリーは T1・L2。「運用の型/12」のようなフォルダつきや、名前の一部でもよい。
    番号だけでは 1 本に決まらないとき（94 の 4 本など）は本文を返さず、候補を返す。
    section: 見出しの番号（例 5-2、§6）か見出しの言葉。空なら全文。
    toc_only: true なら目次（見出し・行・字数）だけ返す。長い節は max_chars で切り、offset で続きを読む。
    """
    with kb.use_media(media):
        return kb.read(file, section, toc_only, max_chars, offset)


@server.tool(title="できる／できないのルールを引く", annotations=LOCAL_CALC)
@_guard
def kb_rules(query: str = "", target: str = "", file: str = "", mark: str = "", max_results: int = 20,
             max_chars: int = 6000, field: str = "", kind: str = "", media: str = "") -> dict:
    """「できる／できない」「上限はいくつ」「この組み合わせは通るか」のルールを 1 行ずつ返す。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    統合表（知識ベースの rules.csv ＝ 92_ルール表_統合.md。14・15 と各メモの「細かいルール一覧」をまとめ直したもの）を先に引く。
    query: 行のどこかに含む語（空白区切りで AND）。新しい id（例 R-入札-029）や元のメモの番号（例 14#66、15:4-41）でも引ける。
    target: 対象（例 P-MAX、デマンド ジェネレーション、検索）かルールの文に含む語。
    field: 分野（例 入札、予算、文字数と本数、オーディエンスとターゲティング、ポリシーと審査）で絞る。
    mark: ✅（公式で確認）／△（公式の中で食い違う）／❓（公式に記載なし）で絞る。
    返す行には id・分野・対象・ルール・値・できる／できない・条件・例外・印・根拠の種類・出典 URL・確認日・元の場所 が付く。
    △ の行には「食い違いの内容」（両方の記述と出典）、期日つきの仕様変更がある行には「変更予定」を必ず付ける。
    file を指定したとき（または統合表が無いとき）は、今までどおり各メモの表から引く（field は使えない）。
    kind: 空なら統合表に加え、query の語で 運用の型の規則（型08）と 核となる理解（90）の当たりも少し添える。
    ルール統合表／知識メモ（各メモの表）／運用の型（体系）（型08 の規則だけ）／核となる理解（90 だけ）で絞れる。
    """
    with kb.use_media(media):
        return kb.rules(query, target, file, mark, max_results, max_chars, field, kind)


@server.tool(title="指標が動いた原因の木を引く", annotations=LOCAL_CALC)
@_guard
def kb_logic_tree(metric: str = "", direction: str = "", id: str = "", max_results: int = 15,
                  max_chars: int = 9000, media: str = "") -> dict:
    """指標と向きから、ロジックツリーの要因の行（要因・なぜ・確かめ方・打ち手・副作用・根拠）を返す。

    media: google（既定）／meta／lineyahoo／chatgpt。その媒体にこの種類のメモ（フォルダ）があるときに引ける（無ければ、無いと返す）。
    2026-11-22 時点で、ロジックツリーがあるのは Google と Meta。

    metric: 指標の名前か略号（例 クリック単価、CPC、コンバージョン率、獲得単価、ROAS）。用語集の別名でも引ける。
    direction: 上（上がった・増えた）／下（下がった・減った）。検索語句の質は 悪／良。空なら両方。
    id: 行の番号（例 CPC-上-07）で 1 行を引く。metric を空にすると、引ける指標の一覧を返す。
    「まず切り分ける順番」の節も一緒に返す。根拠に経験則・推測を含む行は仮説として扱う。
    """
    with kb.use_media(media):
        return kb.logic_tree(metric, direction, id, max_results, max_chars)


@server.tool(title="用語を引く", annotations=LOCAL_CALC)
@_guard
def kb_glossary(term: str, max_results: int = 10, media: str = "") -> dict:
    """用語集（33）から用語を引く。旧称・略語・英語からも引ける（例 部分一致、ROAS、目標アクション単価）。

    media: google（既定）／meta／lineyahoo／chatgpt。その媒体にこの種類のメモ（フォルダ）があるときに引ける（無ければ、無いと返す）。
    用語集が無い媒体の用語は、kb_search に media を付けて探す。

    意味・間違えやすい点・くわしく書いてあるメモを返す。名前が変わった用語の対応表も見る。
    """
    with kb.use_media(media):
        return kb.glossary(term, max_results)


@server.tool(title="Tips を引く", annotations=LOCAL_CALC)
@_guard
def kb_tips(query: str = "", scene: str = "", file: str = "", max_results: int = 10, media: str = "") -> dict:
    """実務家などの Tips（tips/T1〜）を、語や場面で引く。返す行はすべて「仮説」として扱う。

    media: google（既定）／meta／lineyahoo／chatgpt。その媒体にこの種類のメモ（フォルダ）があるときに引ける（無ければ、無いと返す）。

    query: 行のどこかに含む語（空白区切りで AND）。scene: 「場面」の列に含む語。file: T1 などで絞る。
    根拠の種類・誰の主張か・時期・条件と反対意見を一緒に返す。
    """
    with kb.use_media(media):
        return kb.tips(query, scene, file, max_results)


@server.tool(title="主張を知識ベースに照らす", annotations=LOCAL_CALC)
@_guard
def kb_check_claim(claim: str, max_results: int = 15, media: str = "") -> dict:
    """文（これから答えようとしている主張）を渡すと、関係しそうなルール・記述・ツリーの行・Tips を並べて返す。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    判定はしない。並んだ記述と主張を見比べて、合っているか・未確認かを決めるのは呼ぶ側。
    文の中の数字と同じ数字がある行には印を付ける。
    主張が理解度テストの「よくある誤答」に近いときは、先頭の「よくある誤答に近い」に正解と根拠を付けて知らせる。
    """
    with kb.use_media(media):
        return kb.check_claim(claim, max_results)


@server.tool(title="運用の型を引く（規則・しきい値・式・状態）", annotations=LOCAL_CALC)
@_guard
def kb_kata(query: str = "", kind: str = "", state: str = "", max_results: int = 20, max_chars: int = 9000,
            media: str = "") -> dict:
    """「次に何をするか」を、運用の型（運用の型/）から引く。判定はしない（問いと規則を並べる）。

    media: google（既定）／meta／lineyahoo／chatgpt。その媒体にこの種類のメモ（フォルダ）があるときに引ける（無ければ、無いと返す）。
    運用の型/ フォルダが無い媒体は、kb_read(file='28', media=…) と 36 の当てはめを読む。

    kind を省くと query の番号から決める。何も渡さないと引き方と分類の一覧を返す。
    - 規則（型08）: query に 規換-15（番号）／規換（分類）／語（条件・行動の部分一致、空白区切りは AND）。
      state='状4' で「使う状態」の列で絞る。規則の全文の行（条件・行動・理由・例外・根拠の種類・参照・使う状態）と、
      規則が指す 閾NN・式 N の中身を添える。
    - しきい値（型91）: query に 閾08 か語。根拠の種類と、対応する rules.csv の id（91 §7）を必ず返す。〔推論〕には注意を付ける。
    - 式（型90）: query に 式 4 か語。
    - 状態（型06・型08）: kind='状態'。決定木の問いと枝、状態ごとの「主に引く分類」「触ってはいけないもの」。
      state を付けると、その状態に行き着く問いと打ち手。
    - 裁定（型92）／未確認（型93）: kind='裁定' か '未確認'。裁定 3、D2 か語。
    """
    with kb.use_media(media):
        return kata.kata(query, kind, state, max_results, max_chars)


@server.tool(title="理解度テストを引く", annotations=LOCAL_CALC)
@_guard
def kb_quiz(query: str = "", field: str = "", difficulty: str = "", type: str = "", mark: str = "", id: str = "",
            mode: str = "", count: int = 5, shuffle: bool = False, seed: int = 0, max_chars: int = 6000,
            media: str = "") -> dict:
    """理解度テスト（quiz.jsonl）を引く。

    media: google（既定）／meta／lineyahoo／chatgpt。ほかの媒体の知識ベースを引くときに付ける。

    mode='問い'（既定）: 分野（部分一致。例 入札、計測、LP）・難しさ（基本／実務／難）・型（落とし穴、未確認、計算 など）・
    印（✅△❓）・語（問いに含む）で絞り、count 問を返す。正解は返さない（問い・id・分野・型・難しさだけ）。shuffle でばらばらに。
    id を渡す（mode='答え'）: 正解・よくある誤答・なぜ誤答するか・根拠・印・採点の要点。カンマ区切りで複数。
    mode='誤答': query の主張に近い「よくある誤答」を探し、正解と根拠を返す。
    """
    with kb.use_media(media):
        return quiz.quiz(query, field, difficulty, type, mark, id, mode, count, shuffle, seed, max_chars)


# ---- 段 3.5: 小さな道具（計算と点検。外へは何も送らない） ----

@server.tool(title="広告文の文字数と書き方を点検する", annotations=LOCAL_CALC)
@_guard
def ad_text_check(ad_type: str, fields: dict) -> dict:
    """広告文の文字数を Google の数え方（半角 1・全角 2）で数え、種類ごとの上限と本数に照らす。編集基準の目印も返す。

    ad_type: レスポンシブ検索広告（rsa）／サイトリンク／コールアウト／構造化スニペット／P-MAX／
    デマンド ジェネレーション（dg）／レスポンシブ ディスプレイ広告（rda）／
    動画: 動画リーチ（インストリーム。長い見出し〔必須・90〕・説明文・行動を促すフレーズ〔10〕・見出し〔CTA に付く・30〕・表示URL）／
    ショート（動画リーチ）／インフィード動画広告（見出し・説明文は 1 行ずつ渡す）／マストヘッド／
    デマンド ジェネレーション（動画広告。dg動画）／数えるだけ。
    fields: {項目: 文の並び}。例 {"見出し": [...], "説明文": [...], "パス1": "...", "パス2": "..."}。
    サイトリンクは {"リンク文": [...], "説明文1": [...], "説明文2": [...]}（同じ順番で 1 本ずつ）。
    目印: 記号の繰り返し、句読点の連続、飾りの記号、絵文字、半角カタカナ、不自然な空白、同じ文の重複。
    上限が公式の中で食い違うものは両方で判定する。根拠のメモと行を返す。
    """
    return specs.ad_text_check(ad_type, fields)


@server.tool(title="URL を組み立てる・分解する", annotations=LOCAL_CALC)
@_guard
def url_builder(final_url: str = "", utm: dict | None = None, suffix: str = "", extra_params: dict | None = None,
                campaign_type: str = "", parse_url: str = "") -> dict:
    """最終ページ URL・UTM・最終ページ URL のサフィックス・ValueTrack を組み立て、問題を知らせる。URL は開かない。

    utm: {"source": "google", "medium": "cpc", "campaign": "..."}（utm_ を付けても付けなくてもよい）。
    suffix: 足したいサフィックス（? や & で始めない）。extra_params: ほかのパラメータの辞書。
    campaign_type: キャンペーンの種類（例 デマンド ジェネレーション）。渡すと使えない ValueTrack を知らせる。
    知らせること: パラメータの重複、日本語の未エンコード、# の位置、手で入れた gclid、短縮 URL、
    知識ベースの一覧に無い ValueTrack。parse_url を渡すと、その URL を部品に分解して同じ点検をする。
    """
    if parse_url:
        return urltool.parse(parse_url)
    return urltool.build(final_url, utm, suffix, extra_params, campaign_type)


@server.tool(title="採算から獲得単価の上限を出す", annotations=LOCAL_CALC)
@_guard
def unit_economics(sale_price_yen: float | None = None, gross_margin_rate: float | None = None,
                   gross_profit_yen: float | None = None, purchases_per_customer: float = 1,
                   ltv_months: float | None = None, spend_share: float = 1.0, incremental_rate: float = 1.0,
                   close_rate: float = 1.0, cpc_yen: float | None = None, cpa_yen: float | None = None,
                   cvr: float | None = None) -> dict:
    """粗利・LTV・増分率・成約率から、損益分岐の獲得単価、許容できる獲得単価、目標広告費用対効果の下限、回収期間を出す。

    率はすべて 0〜1（40% なら 0.4）。gross_profit_yen か、sale_price_yen と gross_margin_rate を渡す。
    purchases_per_customer: LTV の期間内の平均購入回数。ltv_months: その期間の月数（回収期間に使う）。
    spend_share: 粗利のうち獲得に使ってよい割合（広告主が決める。既定 1 ＝ 損益分岐）。
    incremental_rate: 広告が無ければ起きなかった割合（既定 1。実験が無ければ仮置き）。
    close_rate: メインの成果（問い合わせなど）から成約への率。
    cpc_yen と cpa_yen（または許容の単価）から、必要なコンバージョン率も逆算する。式は 37 の 1-3。
    """
    return calc.unit_economics(sale_price_yen, gross_margin_rate, gross_profit_yen, purchases_per_customer, ltv_months,
                               spend_share, incremental_rate, close_rate, cpc_yen, cpa_yen, cvr)


@server.tool(title="CPA と ROAS を試算する", annotations=LOCAL_CALC)
@_guard
def cpa_roas_simulate(cpc_yen: float | None = None, cvr: float | None = None, monthly_budget_yen: float | None = None,
                      value_per_conversion_yen: float | None = None, gross_margin_rate: float | None = None,
                      target_cpa_yen: float | None = None, target_roas: float | None = None,
                      target_conversions: float | None = None, max_monthly_clicks: float | None = None,
                      saturation_spend_yen: float | None = None, cpc_multipliers: list[float] | None = None,
                      cvr_multipliers: list[float] | None = None, budget_multipliers: list[float] | None = None,
                      ctr: float | None = None, industry: str = "", benchmark_media: str = "google",
                      usd_jpy: float | None = None) -> dict:
    """数字を動かすと、件数・CPA・ROAS・利益がどう変わるかを試算する（手元の計算。アカウントは読まない）。

    順方向: 月の予算・クリック単価・コンバージョン率（0〜1）・1 件あたりの売上・粗利率から、クリック数・件数・CPA・売上・ROAS・
    粗利・広告費を引いた利益・損益分岐の CPA と ROAS（unit_economics を使う）。
    逆算: target_cpa_yen か target_roas（倍）で「要るコンバージョン率」「出せるクリック単価の上限」、target_conversions で要る予算。
    感度の表: クリック単価とコンバージョン率を倍率で動かした CPA・ROAS の表（既定 0.8〜1.2 倍）。
    頭打ち（任意）: max_monthly_clicks（検索で探されている数の上限）、saturation_spend_yen（出すほど効きが鈍る形の「鈍り始める額」）。
    入れると、平均の CPA と最後に足した分の CPA を分けて返し、予算 ×1・×1.5・×2（budget_multipliers）の案を並べる。
    頭打ちの値は渡された値で、実績が無ければ仮置き。考え方は非公開の判断の型のメモ。
    industry（業種。例 不動産、アパレル）と benchmark_media（google か facebook）を渡すと、cpc_yen・cvr・ctr のうち渡していないものに
    業種平均を入れる（米国の中央値〔WordStream・LocaliQ〕、1 ドル usd_jpy 円〔既定 150 の仮置き〕で換算。日本の平均ではない）。
    ctr を渡すと表示回数と CPM も出す。
    """
    return simulate.cpa_roas_simulate(monthly_budget_yen, cpc_yen, cvr, value_per_conversion_yen, gross_margin_rate,
                                      target_cpa_yen, target_roas, target_conversions, max_monthly_clicks,
                                      saturation_spend_yen, cpc_multipliers, cvr_multipliers, budget_multipliers,
                                      ctr, industry, benchmark_media, usd_jpy)


@server.tool(title="予算の配分を試算する", annotations=LOCAL_CALC)
@_guard
def media_plan_simulate(months: list[str], search_layers: list[dict] | None = None, channels: list[dict] | None = None,
                        demand: list[float] | None = None, month_weight: list[float] | None = None, step_yen: float = 50000,
                        current_total_yen: float | None = None, current_conversions: float | None = None,
                        scenarios: dict | None = None, targets: list[float] | None = None, fixed_yen: dict | None = None,
                        reach: dict | None = None, unit: str = "件", save_chart: bool = False) -> dict:
    """月 × 媒体に予算を配ったときの件数を試算する（メディアプラン。手元の計算）。

    search_layers: 検索の層の並び（意図の高い順）。各 {id, name, vol〔月の検索数〕, share〔表示される割合〕, ctr, cpc, cvr, brand〔指名なら true〕}
    か {id, cap_yen〔月の上限額〕, cpc, cvr}。channels: 検索以外の媒体 {id, name, cpc, cvr, sat_yen〔鈍り始める額〕, cap_yen〔月の上限〕, seasonal}。
    demand: 月ごとの検索の量（季節の倍率にする）。month_weight: 月の重み（価値の高い月を重くする）。
    step_yen（既定 5 万円）ずつ、次の 1 件がいちばん安い所へ足す道を作り、総額で切って案を出す。
    current_total_yen を渡すと A 配り直す／B 件数 1.5 倍／C 2 倍の案（scenarios で上書き可）。targets: 要る件数から要る媒体費。
    reach: {population, channels: {媒体: {cpm, on_target}}, budgets_yen} で届く割合と回数。save_chart で曲線の図（SVG）を out/ に保存。
    元は外部のコードで、2026-11-18 に運用者の判断で取り込んだ（非公開の判断の型のメモ）。
    """
    return mediaplan.media_plan_simulate(months, search_layers, channels, demand, month_weight, step_yen, current_total_yen,
                                         current_conversions, scenarios, targets, fixed_yen, reach, unit, save_chart)


@server.tool(title="実績の点に近似曲線を当てる", annotations=READ_ONLY)
@_guard
def fit_response_curve(costs: list[float] | None = None, values: list[float] | None = None, value_name: str = "クリック数",
                       months: list[str] | None = None, customer_id: str = "", start: str = "", end: str = "", name: str = "",
                       save_chart: bool = False) -> dict:
    """月ごとの費用と成果（クリック数・表示回数・成果の件数）の点に、比例・頭打ち（c・x ÷ (x ＋ b)）・鈍る（c・(1 − e^(−x ÷ S))）の
    3 つの形を当て、決定係数で選ぶ（頭打ちの形は比例より 0.02 以上高いときだけ）。点が 4 つ未満なら当てない。
    costs・values を手で渡すか、customer_id・start・end を渡すと Google 広告から月ごとの実績を読む（アカウント全体、読むだけ）。
    鈍る形の S は、cpa_roas_simulate の saturation_spend_yen と media_plan_simulate の sat_yen にそのまま入れられる。
    save_chart で点と曲線の図（SVG）を out/ に保存。元は外部のコード（非公開の判断の型のメモ）。
    """
    return curvefit.fit_response_curve(costs, values, value_name, months, customer_id, start, end, name, save_chart)


@server.tool(title="予算のペースを見る", annotations=LOCAL_CALC)
@_guard
def budget_pace(monthly_budget_yen: float, spent_yen: float, remaining_days: int, days_in_month: int | None = None,
                current_daily_budget_yen: float | None = None, month: str = "") -> dict:
    """月の予算・今日までの費用・残り日数から、ペース（%）、月末の見込み、日予算の案を出す。

    remaining_days: 費用にまだ入っていない日数（今日の分が費用に入っていなければ今日を含める）。
    days_in_month を省くと month（YYYY-MM。省くと今月）の日数を使う。
    current_daily_budget_yen を渡すと、1 日 2 倍・月 30.4 倍の上限で月の予算を超えうるかも見る。
    """
    return calc.budget_pace(monthly_budget_yen, spent_yen, remaining_days, days_in_month, current_daily_budget_yen, month)


@server.tool(title="画像の寸法を要件に照らす", annotations=LOCAL_CALC)
@_guard
def image_spec_check(width: int | None = None, height: int | None = None, size_kb: float | None = None,
                     file_format: str = "", path: str = "", formats: list[str] | None = None) -> dict:
    """画像の幅・高さ・容量・形式が、どのフォーマット・枠の要件に合うかを返す。

    対象: レスポンシブ ディスプレイ広告、デマンド ジェネレーション、P-MAX、画像アセット（検索）、
    アップロード型のイメージ広告。path を渡すと手元の画像ファイルを測る（Pillow が無くても PNG・JPEG・GIF・WebP は読める）。
    formats でフォーマットを絞れる。比率が合う枠について、寸法・容量・形式の合否と根拠を返す。
    """
    return specs.image_spec_check(width, height, size_kb, file_format, path, formats)


@server.tool(title="有意かどうかを計算する", annotations=LOCAL_CALC)
@_guard
def significance(mode: str, success: int | None = None, total: int | None = None, a_success: int | None = None,
                 a_total: int | None = None, b_success: int | None = None, b_total: int | None = None,
                 baseline_rate: float | None = None, target_rate: float | None = None, count: int | None = None,
                 expected: float | None = None, confidence: float = 0.95, power: float = 0.8) -> dict:
    """率や件数のぶれを計算し、平易な一文でも返す。暗算の代わりに使う。

    mode:
    - 率の幅: success / total から、成果率・クリック率の信頼区間（ウィルソン）
    - 2案の差: a_success / a_total / b_success / b_total から、差があると言えるか（z 検定。件数が少なければフィッシャー）
    - 必要な件数: baseline_rate / target_rate から、1 案あたりに必要な件数
    - 0件の上限: total 件で 0 件だったときの、本当の率の上限（3 の法則）
    - 件数の振れ幅: count（今回の件数）と expected（いつもの平均）から、偶然で出る範囲（ポアソン）
    confidence: 信頼度（既定 0.95）。power: 検出力（既定 0.8）。
    """
    return stats.significance(mode, success, total, a_success, a_total, b_success, b_total, baseline_rate, target_rate,
                              count, expected, confidence, power)


@server.tool(title="2 つの一覧を突き合わせる", annotations=LOCAL_CALC)
@_guard
def list_compare(list_a: list[str], list_b: list[str], mode: str = "差分", ignore_width: bool = True,
                 ignore_space: bool = True, ignore_case: bool = True, ignore_match_marks: bool = False,
                 label_a: str = "A", label_b: str = "B", max_items: int = 100) -> dict:
    """2 つの一覧（キーワード、検索語句、除外キーワードなど）の、両方にあるもの・片方だけのもの・中の重複を返す。

    表記ゆれ（全角半角、空白、大文字小文字）をそろえて比べる（それぞれ切り替えられる）。
    ignore_match_marks: true なら [ ] " - の一致方式の記号を無視する。
    mode=除外の点検: list_a に登録キーワード、list_b に除外キーワードを渡すと、除外がキーワードを止めていないかを見る
    （[語] 完全一致、"語" フレーズ一致、それ以外は部分一致として読む）。
    """
    return calc.list_compare(list_a, list_b, mode, ignore_width, ignore_space, ignore_case, ignore_match_marks,
                             label_a, label_b, max_items)


@server.tool(title="商品フィードを点検する", annotations=LOCAL_CALC)
@_guard
def feed_check(row: dict | None = None, csv_path: str = "", max_rows: int = 20) -> dict:
    """商品フィードの 1 行（{属性: 値} の辞書）か、CSV・TSV のパスを渡すと、必須の欠け・字数・価格や在庫の書式の問題を返す。

    点検するのは知識ベース 21 に書かれている決まりだけ（書かれていない決まりは点検しない）。
    属性名は Merchant Center の英語の列名（id、title、price など）。所見ごとに根拠の行を返す。
    """
    return feed.feed_check(row, csv_path, max_rows)


# ---- 動画（2026-11-18。外へは何も送らない） ----

@server.tool(title="動画の作り方の指針を引く（ABCD）", annotations=LOCAL_CALC)
@_guard
def kb_video_guide(objective: str = "", format: str = "", length_sec: float | None = None, letter: str = "",
                   query: str = "", include_numbers: bool = True, max_results: int = 40, phase: str = "") -> dict:
    """動画広告の作り方のノウハウを知識ベースから引く。呼ぶたびにメモを読み直す。
    中身（何を入れるか）: Google・YouTube 公式の ABCD の 11 項目、目的別、ショート、バンパー、フォーマット別、
    自動で見られる 4 属性、安全な範囲（38）。作り方（どう作るか）: 企画・台本と絵コンテ・撮影と素材・編集・音・
    Google の作成ツール・アップロードと入稿・検証と改善・点検表の工程ごとのやることと確認の問い（40）。

    phase: 企画／台本・絵コンテ／撮影・素材／編集／音／Google の作成ツール／アップロードと入稿／検証と改善／点検表
    （渡すとその工程の行だけ。省くと 38 と 40 の全部から絞る）。

    objective: 認知／比較検討／行動（省くと全部）。format: ショート／バンパー／インフィード／スキップ可能／スキップ不可／
    デマンド ジェネレーション／P-MAX／動画リーチ／動画再生回数／テレビ画面。length_sec: 尺（秒）で当てはまる行に絞る。
    letter: A・B・C・D で原則を絞る。query: 語（AND）。各行に「確認の問い」と根拠の行（ファイル・行番号・確認日）が付く。
    include_numbers: 公式が示す数字（誰が・いつ・どれだけで測ったか つき）も返す。数字は仮説として扱う。
    """
    return abcd.guide(objective, format, length_sec, letter, query, include_numbers, max_results, phase=phase)


@server.tool(title="台本・絵コンテを ABCD の観点で見直す", annotations=LOCAL_CALC)
@_guard
def video_storyboard_check(brand_name: str, scenes: list[dict] | None = None, script: str = "", objective: str = "",
                           format: str = "", length_sec: float | None = None, has_audio: bool | None = None) -> dict:
    """台本か絵コンテを渡すと、ABCD の 11 項目と、Google 広告が自動で見る 4 属性について、
    台本から機械で拾えた目印（見つかったもの・見つからなかったもの）と「確認の問い」と根拠の行を返す。判定はしない。

    scenes: [{"開始": 0, "終了": 2, "画": "…", "文字": "…", "音声": "…", "音": "…", "行動": "…"}, …]（英語の start/end/visual/text/audio/cta も可）。
    または script: 各行「0-2秒 画: … 文字: … 音声: … 行動: …」の文。brand_name: ブランド名（最初の 5 秒に出るかなどを見る）。
    objective・format を渡すと、目的別・フォーマット別の問いも足す。has_audio=false なら無音として扱う。
    """
    return abcd.storyboard_check(scenes, script, brand_name, objective, format, length_sec, has_audio)


@server.tool(title="動画ファイルの仕様を点検する", annotations=LOCAL_CALC)
@_guard
def video_spec_check(path: str = "", duration_sec: float | None = None, width: int | None = None, height: int | None = None,
                     size_mb: float | None = None, file_format: str = "", has_audio: bool | None = None,
                     formats: list[str] | None = None) -> dict:
    """動画の長さ・縦横比・解像度・容量・形式・音声の有無を、フォーマットごとの要件（スキップ可能／スキップ不可／
    バンパー／インフィード／ショート／デマンド ジェネレーション／P-MAX／マストヘッド）と、
    キャンペーンの推奨の組み合わせ（動画リーチ・動画再生回数）に照らす。根拠は知識ベース 39 の行で、毎回引き直す。

    path: 手元の動画ファイル（ffprobe → ffmpeg → MP4・MOV の箱を自分で読む、の順で測る。外へは送らない）。
    または duration_sec・width・height・size_mb・file_format を数字で渡す。formats でフォーマットを絞れる。
    知識ベースに無い決まりは「未確認」と返す。公式の中で食い違う上限は両方で判定する。
    """
    return video.video_spec_check(path, duration_sec, width, height, size_mb, file_format, has_audio, formats)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
