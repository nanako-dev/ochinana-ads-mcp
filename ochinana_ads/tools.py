"""道具 5 個の中身。どれも読み取りだけで、アカウントを変える呼び出しは持たない。

返答の形（共通）: 先頭に 対象アカウント・期間・単位。続いて合計、件数、上位の行。
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional  # noqa: F401

from . import convert, gaql
from .client import SetupError, accessible_customer_ids, credentials_file_status, explain_api_error, load_client, search
from .config import (
    ALLOWED_CUSTOMER_IDS,
    DEFAULT_TOP_N,
    MAX_TOP_N,
    PRINCIPLES_PATH,
    UPDATED,
    VERSION,
    out_dir,
)

Search = Callable[[str, str], List]

_client = None
_accounts: Dict[str, dict] = {}


def _search(customer_id: str, query: str) -> List:
    global _client
    if _client is None:
        _client = load_client()
    return list(search(_client, customer_id, query))


def _metrics(r) -> dict:
    m = r.metrics
    return {
        "impressions": int(m.impressions),
        "clicks": int(m.clicks),
        "cost_micros": int(m.cost_micros),
        "conversions": float(m.conversions),
        "conversions_value": float(m.conversions_value),
    }


def _account(cid: str, run: Search) -> dict:
    """アカウントの名前・通貨・タイムゾーン。同じ起動の中では 1 回だけ読む。"""
    if cid not in _accounts:
        rows = run(cid, gaql.account_query())
        if not rows:
            raise ValueError(f"アカウント {cid} の情報が返らなかった")
        c = rows[0].customer
        _accounts[cid] = {
            "ID": cid,
            "名前": c.descriptive_name,
            "通貨": c.currency_code,
            "タイムゾーン": c.time_zone,
            "管理アカウント": bool(c.manager),
        }
    return _accounts[cid]


def _label(acct: dict) -> str:
    return f"{acct['ID']}（{acct['名前']}）"


def _period(start: str, end: str, acct: dict) -> str:
    return f"{start}〜{end}（{acct['タイムゾーン']} の日付）"


def _defaults(cid: str, acct: dict, run: Search) -> dict:
    """国と言語の既定値。配信中の設定でいちばん多いものを取り、無ければ通貨とタイムゾーンから決める。"""
    geos, langs = [], []
    for r in run(cid, gaql.targeting_query()):
        cc = r.campaign_criterion
        if cc.type_.name == "LOCATION":
            geos.append(cc.location.geo_target_constant)
        elif cc.type_.name == "LANGUAGE":
            langs.append(cc.language.language_constant)
    geo, lang = convert.pick_most_common(geos), convert.pick_most_common(langs)
    out: dict = {}
    if geo:
        g = run(cid, gaql.geo_name_query(geo))[0].geo_target_constant
        out["既定の国"] = {"国コード": g.country_code, "配信地域": g.name, "地域 ID": int(g.id)}
        geo_basis = "キャンペーンの配信地域でいちばん多い指定"
    elif acct["通貨"] == "JPY" and acct["タイムゾーン"] == "Asia/Tokyo":
        out["既定の国"] = {"国コード": "JP", "配信地域": "Japan", "地域 ID": 2392}
        geo_basis = "配信地域の指定が無いので、通貨とタイムゾーンから日本とした"
    else:
        out["既定の国"] = None
        geo_basis = "決められない（配信地域の指定が無く、通貨とタイムゾーンからも分からない）"
    if lang:
        l = run(cid, gaql.language_name_query(lang))[0].language_constant
        out["既定の言語"] = {"言語コード": l.code, "言語": l.name, "言語 ID": int(l.id)}
        lang_basis = "キャンペーンの言語でいちばん多い指定"
    elif acct["通貨"] == "JPY" and acct["タイムゾーン"] == "Asia/Tokyo":
        out["既定の言語"] = {"言語コード": "ja", "言語": "Japanese", "言語 ID": 1005}
        lang_basis = "言語の指定が無いので、通貨とタイムゾーンから日本語とした"
    else:
        out["既定の言語"] = None
        lang_basis = "決められない"
    out["既定値の根拠"] = {"国": geo_basis, "言語": lang_basis}
    return out


def list_accounts(run: Optional[Search] = None) -> dict:
    run = run or _search
    accounts, failed = [], []
    for cid in ALLOWED_CUSTOMER_IDS:
        try:
            acct = dict(_account(cid, run))
            acct.update(_defaults(cid, acct, run))
            accounts.append(acct)
        except (ValueError, SetupError):
            raise
        except Exception as ex:  # 片方だけ読めないときも、もう片方は返す
            failed.append({"ID": cid, "読めなかった理由": explain_api_error(ex)})
    out = {
        "対象アカウント": f"許可リストの {len(ALLOWED_CUSTOMER_IDS)} 件（{', '.join(ALLOWED_CUSTOMER_IDS)}）",
        "期間": "指定なし（いまの設定）",
        "単位": "アカウント",
        "アカウント": accounts,
    }
    if failed:
        out["読めなかったアカウント"] = failed
    out["道具の版"] = f"{VERSION}（{UPDATED}）"
    return out


def check_connection(run: Optional[Search] = None, accessible: Optional[Callable[[], List[str]]] = None,
                     meta_client=None) -> dict:
    """Google の段と、Meta 広告の段をまとめて返す。Meta を用意していなければ、何が要るかを返すだけ（Google の結果は変えない）。"""
    from . import meta

    out = _check_google(run, accessible)
    try:
        out["Meta 広告"] = meta.check(meta_client)
    except (ValueError, meta.MetaError) as ex:
        out["Meta 広告"] = {"結論": f"Meta の確かめで止まった: {ex}"}
    return out


def _check_google(run: Optional[Search] = None, accessible: Optional[Callable[[], List[str]]] = None) -> dict:
    """つないだあとの確かめ。設定 → API → 許可リストの各アカウント、の順に見る。

    許可リストに無いアカウントの ID と名前は返さない（同じログインで勤務先や取引先のアカウントにも届くことがあるため。
    一覧は、本人が端末で `connect check` を動かして見る）。
    """
    run = run or _search
    steps: List[dict] = []
    out: dict = {"対象アカウント": f"許可リストの {len(ALLOWED_CUSTOMER_IDS)} 件", "期間": "指定なし（いまの設定）",
                 "単位": "確かめの段ごと", "道具の版": f"{VERSION}（{UPDATED}）"}
    f = credentials_file_status()
    steps.append({"段": "1. 認証ファイル", "結果": "よい" if f.get("ファイル") == "ある" else "直す", **f})
    if f.get("ファイル") != "ある":
        out["段"] = steps
        out["結論"] = "認証ファイルが無いので、ここで止めた。README の「入れ方」の 5〜7 を見る。"
        return out
    direct: Optional[set] = None
    try:
        if accessible is None:
            global _client
            if _client is None:
                _client = load_client()
            ids = accessible_customer_ids(_client)
        else:
            ids = accessible()
        direct = set(ids)
        steps.append({"段": "2. Google 広告 API", "結果": "よい",
                      "このログインで直接読めるアカウント": f"{len(ids)} 件（ID と名前はここに出さない。本人が端末で connect check を動かすと見られる）"})
    except SetupError:
        raise
    except Exception as ex:
        reason = explain_api_error(ex) if getattr(ex, "failure", None) is not None else _auth_reason(ex)
        steps.append({"段": "2. Google 広告 API", "結果": "直す", "理由": reason})
        out["段"] = steps
        out["結論"] = "API につながらないので、ここで止めた。理由の「直し方」を見る。"
        return out
    if not ALLOWED_CUSTOMER_IDS:
        steps.append({"段": "3. 許可リスト", "結果": "直す",
                      "理由": "環境変数 ADS_ALLOWED_CUSTOMER_IDS が空。本人が端末で connect check を動かし、読むアカウントを選んで出た 1 行を設定に足す"})
        out["段"] = steps
        out["結論"] = "API にはつながる。読むアカウントがまだ決まっていない。"
        return out
    rows = []
    for cid in ALLOWED_CUSTOMER_IDS:
        r: dict = {"ID": cid}
        try:
            acct = _account(cid, run)
            r.update({"結果": "読める", "名前": acct["名前"], "通貨": acct["通貨"], "タイムゾーン": acct["タイムゾーン"],
                      "マネージャー": "はい" if acct["管理アカウント"] else "いいえ"})
            if acct["通貨"] and acct["通貨"] != "JPY":
                r["注意"] = f"通貨が {acct['通貨']}。この道具は円のアカウント向けで、実績を読む道具は円以外のアカウントを断る"
            if acct["管理アカウント"]:
                r["注意"] = "マネージャーのアカウントには実績が無い。配下のアカウントの ID を許可リストに入れる"
        except (ValueError, SetupError):
            raise
        except Exception as ex:
            r.update({"結果": "読めない", "理由": explain_api_error(ex) if getattr(ex, "failure", None) is not None else _auth_reason(ex)})
        if direct is not None:
            r["直接の権限"] = "ある" if cid in direct else "無い（マネージャー経由。認証ファイルに login_customer_id が要る）"
        rows.append(r)
    steps.append({"段": "3. 許可リストのアカウント", "結果": "よい" if all(x["結果"] == "読める" for x in rows) else "直す", "アカウント": rows})
    out["段"] = steps
    ok = all(x["結果"] == "読める" for x in rows)
    out["結論"] = ("つながった。許可リストのアカウントを全部読める。キーワードプランナー（forecast_volume）だけは、"
                 "Cloud プロジェクトの利用権限が Basic 以上のときに動く。") if ok else "読めないアカウントがある。行の「理由」を見る。"
    return out


def _auth_reason(ex: Exception) -> str:
    from .client import explain_auth_error

    if type(ex).__module__.startswith("google.auth"):
        return explain_auth_error(ex)
    return f"API を呼べなかった（{type(ex).__name__}）"


def _performance_rows(level: str, api_rows: List) -> List[dict]:
    rows = []
    for r in api_rows:
        d: dict = {"キャンペーン": r.campaign.name}
        if level == "campaign":
            d["種類"] = convert.ja(convert.CHANNEL_JA, r.campaign.advertising_channel_type.name)
            d["状態"] = convert.ja(convert.STATUS_JA, r.campaign.status.name)
        elif level == "ad_group":
            d["広告グループ"] = r.ad_group.name
            d["状態"] = convert.ja(convert.STATUS_JA, r.ad_group.status.name)
        else:
            c = r.ad_group_criterion
            d["広告グループ"] = r.ad_group.name
            d["キーワード"] = c.keyword.text
            d["一致方式"] = convert.ja(convert.MATCH_JA, c.keyword.match_type.name)
            d["状態"] = convert.ja(convert.STATUS_JA, c.status.name)
        rows.append({**d, **_metrics(r)})
    return rows


def get_performance(customer_id: str, start: str, end: str, level: str = "campaign",
                    top_n: int = DEFAULT_TOP_N, run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    level = gaql.normalize_level(level)
    query = gaql.performance_query(level, start, end)
    top_n = convert.clamp_top_n(top_n, DEFAULT_TOP_N, MAX_TOP_N)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    body, _ = convert.report(_performance_rows(level, run(cid, query)), top_n)
    out = {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct),
        "単位": f"{gaql.LEVEL_JA[level]}ごとの期間合計。金額は円",
        **body,
    }
    if level == "keyword":
        out["注記"] = [
            "キーワードを介さない配信（AI Max のキーワードなしの拡張、P-MAX など）はここに入らないので、"
            "合計はキャンペーン単位の合計より小さくなることがある。"
        ]
    return out


def get_search_terms(customer_id: str, start: str, end: str, top_n: int = DEFAULT_TOP_N,
                     sort_by: str = "費用", run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    query = gaql.search_terms_query(start, end)
    top_n = convert.clamp_top_n(top_n, DEFAULT_TOP_N, MAX_TOP_N)
    if sort_by not in convert.SORT_KEYS:
        raise ValueError("並び順は 費用 / クリック / 表示 のどれか")
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    rows = []
    for r in run(cid, query):
        rows.append({
            "検索語句": r.search_term_view.search_term,
            "キャンペーン": r.campaign.name,
            "広告グループ": r.ad_group.name,
            "追加・除外の状態": convert.ja(convert.TERM_STATUS_JA, r.search_term_view.status.name),
            **_metrics(r),
        })
    body, active = convert.report(rows, top_n, sort_by)
    saved = None
    if active:
        saved = convert.write_csv(
            out_dir() / f"search_terms_{cid}_{start}_{end}.csv", [convert.to_ja_row(r) for r in active]
        )
    return {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct),
        "単位": "検索語句 × 広告グループごとの期間合計。金額は円",
        **body,
        "全件の保存先": saved,
        "注記": [
            "検索数の少ない語句は Google 側で伏せられるので、ここの合計はキャンペーンの合計より小さくなる。",
            "検索キャンペーンの語句だけ。P-MAX の語句は含まない。",
        ],
    }


def _keyword_rows(cid: str, run: Search) -> List[dict]:
    q = gaql.keywords_queries()
    rows: List[dict] = []
    for r in run(cid, q["ad_group"]):
        c = r.ad_group_criterion
        rows.append({
            "キーワード": c.keyword.text,
            "一致方式": convert.ja(convert.MATCH_JA, c.keyword.match_type.name),
            "状態": convert.ja(convert.STATUS_JA, c.status.name),
            "置き場所": "広告グループ",
            "キャンペーン": r.campaign.name,
            "広告グループ": r.ad_group.name,
            "negative": bool(c.negative),
        })
    for r in run(cid, q["campaign"]):
        c = r.campaign_criterion
        rows.append({
            "キーワード": c.keyword.text,
            "一致方式": convert.ja(convert.MATCH_JA, c.keyword.match_type.name),
            "状態": "有効",
            "置き場所": "キャンペーン",
            "キャンペーン": r.campaign.name,
            "広告グループ": None,
            "negative": bool(c.negative),
        })
    used: Dict[int, List[str]] = {}
    for r in run(cid, q["shared_list_usage"]):
        used.setdefault(int(r.shared_set.id), []).append(r.campaign.name)
    for r in run(cid, q["shared_list"]):
        c = r.shared_criterion
        campaigns = sorted(used.get(int(r.shared_set.id), []))
        account_wide = r.shared_set.type_.name == "ACCOUNT_LEVEL_NEGATIVE_KEYWORDS"
        rows.append({
            "キーワード": c.keyword.text,
            "一致方式": convert.ja(convert.MATCH_JA, c.keyword.match_type.name),
            "状態": "有効" if (campaigns or account_wide) else "どのキャンペーンにも未適用",
            "置き場所": f"共有の除外リスト「{r.shared_set.name}」" + ("（アカウント全体）" if account_wide else ""),
            "キャンペーン": "、".join(campaigns) if campaigns else None,
            "広告グループ": None,
            "negative": True,
        })
    return rows


def get_keywords(customer_id: str, top_n: int = DEFAULT_TOP_N, run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    top_n = convert.clamp_top_n(top_n, DEFAULT_TOP_N, MAX_TOP_N)
    acct = _account(cid, run)
    normal, negative = convert.split_keywords(_keyword_rows(cid, run))
    order = lambda r: (r["キャンペーン"] or "", r["広告グループ"] or "", r["置き場所"], r["キーワード"])  # noqa: E731
    normal.sort(key=order)
    negative.sort(key=order)
    saved = {}
    if normal:
        saved["キーワード"] = convert.write_csv(out_dir() / f"keywords_{cid}.csv", normal)
    if negative:
        saved["除外キーワード"] = convert.write_csv(out_dir() / f"negative_keywords_{cid}.csv", negative)
    return {
        "対象アカウント": _label(acct),
        "期間": "指定なし（いまの設定。削除済みは除く）",
        "単位": "キーワード 1 個ごと。実績の数字は含まない",
        "件数": {
            "キーワード": len(normal),
            "除外キーワード": len(negative),
            "キーワードの状態別": convert.count_by(normal, "状態"),
            "キーワードの一致方式別": convert.count_by(normal, "一致方式"),
            "除外キーワードの置き場所別": convert.count_by(negative, "置き場所"),
            "表示している件数": {"キーワード": min(top_n, len(normal)), "除外キーワード": min(top_n, len(negative))},
        },
        "並び順": "キャンペーン、広告グループ、キーワードの名前順",
        "キーワード": normal[:top_n],
        "除外キーワード": negative[:top_n],
        "全件の保存先": saved or None,
    }


def get_principles() -> str:
    if not PRINCIPLES_PATH.is_file():
        raise ValueError(f"原則の文書が見つからない: {PRINCIPLES_PATH}")
    return PRINCIPLES_PATH.read_text(encoding="utf-8")


# ================= 段 2 =================
import json as _json  # noqa: E402
import urllib.parse as _urlparse  # noqa: E402

from . import checks, decisions, fetch, lp, stats  # noqa: E402

AD_TYPE_JA = {"RESPONSIVE_SEARCH_AD": "レスポンシブ検索広告", "DEMAND_GEN_MULTI_ASSET_AD": "デマンド ジェネレーション広告",
              "RESPONSIVE_DISPLAY_AD": "レスポンシブ ディスプレイ広告", "EXPANDED_TEXT_AD": "拡張テキスト広告"}
APPROVAL_JA = {"APPROVED": "承認済み", "APPROVED_LIMITED": "承認済み（制限付き）", "DISAPPROVED": "不承認",
               "AREA_OF_INTEREST_ONLY": "承認済み（地域限定）", "UNKNOWN": "不明", "UNSPECIFIED": "未判定"}
REVIEW_JA = {"REVIEWED": "審査済み", "REVIEW_IN_PROGRESS": "審査中", "UNDER_APPEAL": "再審査中",
             "ELIGIBLE_MAY_SERVE": "審査中（配信は可能）", "UNKNOWN": "不明", "UNSPECIFIED": "未判定"}
LINK_STATUS_JA = {"ELIGIBLE": "配信可能", "PAUSED": "一時停止", "REMOVED": "削除済み", "PENDING": "審査中",
                  "LIMITED": "制限付き", "NOT_ELIGIBLE": "配信不可", "UNKNOWN": "不明", "UNSPECIFIED": "未判定"}
FIELD_JA = {"HEADLINE": "見出し", "DESCRIPTION": "説明文", "LONG_HEADLINE": "長い見出し"}
LABEL_JA = {"BEST": "最良", "GOOD": "良", "LOW": "低", "LEARNING": "学習中", "PENDING": "判定待ち",
            "UNKNOWN": "不明", "UNSPECIFIED": "未判定"}
PAGE_SOURCE_JA = {"ADVERTISER": "広告主が指定", "AUTOMATIC": "Google が自動で選択"}
ASSET_SOURCE_JA = {"ADVERTISER": "広告主", "AUTOMATICALLY_CREATED": "自動で作成", "UNKNOWN": "不明", "UNSPECIFIED": "不明"}
AUTO_SOURCE = "AUTOMATICALLY_CREATED"
ORIGIN_ADVERTISER = "入稿した文をつないだもの"
ORIGIN_GOOGLE = "Google が足した文を含む"
ORIGIN_GOOGLE_OTHER_GROUP = "Google が足した文を含む（同じキャンペーンの別の広告グループで足された文）"
ORIGIN_OTHER = "入稿した文とも Google が足した文とも一致しない部分がある"
ORIGIN_UNKNOWN = "見分けられない（この広告グループの文を読めなかった）"
_SEPARATORS = re.compile(r"[\s|｜\-–—・/／:：,、。.!！?？]+")
_BAR = re.compile(r"\s*[|｜]\s*")  # 区切りの縦棒は、文では「 | 」、組み合わせの見出しでは「｜」で出ることがある


def _norm(text: str) -> str:
    return _BAR.sub("|", text or "").strip()


def _is_generated(t: dict) -> bool:
    """Google が足した文か。つながりの側（広告とアセットの結び付き）か、アセットそのものの出どころが自動で作成。"""
    return AUTO_SOURCE in (t.get("link_source"), t.get("asset_source"))


def _headline_origin(headline: str, advertiser: set, generated: set) -> str:
    """組み合わせの見出しが、入稿した文をつないだものか、Google が足した文を含むかを、文字の一致で見分ける。"""
    if not advertiser and not generated:
        return ORIGIN_UNKNOWN
    gen = {_norm(t) for t in generated}
    rest, has_gen = _norm(headline), False
    for t in sorted({_norm(t) for t in advertiser} | gen, key=len, reverse=True):
        if t and t in rest:
            rest = rest.replace(t, " ")
            has_gen = has_gen or t in gen
    if _SEPARATORS.sub("", rest):
        return ORIGIN_OTHER
    return ORIGIN_GOOGLE if has_gen else ORIGIN_ADVERTISER
LP_MAX_URLS = 3
LP_REUSE_HOURS = 6


def _extension_summary(links: List[dict]) -> dict:
    """表示オプションを種類ごとに数える。主な 4 種類は 0 件でも必ず出す。"""
    out = {checks.EXTENSION_JA[t]: {"件数": 0} for t in checks.MAIN_EXTENSIONS}
    for link in links:
        name = checks.EXTENSION_JA.get(link["field_type"], link["field_type"])
        row = out.setdefault(name, {"件数": 0})
        row["件数"] += 1
        state = convert.ja(LINK_STATUS_JA, link["primary_status"])
        row.setdefault("状態", {})
        row["状態"][state] = row["状態"].get(state, 0) + 1
        if link["approval"] not in ("APPROVED", "UNSPECIFIED", "UNKNOWN"):
            row.setdefault("審査", {})
            a = convert.ja(APPROVAL_JA, link["approval"])
            row["審査"][a] = row["審査"].get(a, 0) + 1
    return out


def get_ads_and_assets(customer_id: str, only_enabled: bool = True, include_text: bool = True,
                       run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    acct = _account(cid, run)
    all_campaigns = [
        {"id": int(r.campaign.id), "name": r.campaign.name, "status": r.campaign.status.name,
         "channel": r.campaign.advertising_channel_type.name}
        for r in run(cid, gaql.campaign_settings_query())
    ]
    groups, ads, links = fetch.ad_groups(cid, run), fetch.ads(cid, run), fetch.asset_links(cid, run)
    keep = (lambda s: s == "ENABLED") if only_enabled else (lambda s: True)
    campaigns_out = []
    for camp in sorted((c for c in all_campaigns if keep(c["status"])), key=lambda c: c["name"]):
        groups_out = []
        for g in sorted((g for g in groups if g["campaign_id"] == camp["id"] and keep(g["status"])), key=lambda g: g["name"]):
            ads_out = []
            for ad in (a for a in ads if a["ad_group_id"] == g["id"] and keep(a["status"])):
                row = {
                    "広告 ID": ad["ad_id"],
                    "種類": convert.ja(AD_TYPE_JA, ad["type"]),
                    "状態": convert.ja(convert.STATUS_JA, ad["status"]),
                    "有効性": convert.ja(checks.STRENGTH_JA, ad["strength"]),
                    "審査": f"{convert.ja(APPROVAL_JA, ad['approval'])}（{convert.ja(REVIEW_JA, ad['review'])}）",
                    "見出しの本数": len(ad["headlines"]),
                    "説明文の本数": len(ad["descriptions"]),
                    "最終 URL": ad["final_urls"][0] if ad["final_urls"] else None,
                    "表示パス": "/".join(p for p in (ad["path1"], ad["path2"]) if p) or None,
                }
                if include_text:
                    row["見出し"] = ad["headlines"]
                    row["説明文"] = ad["descriptions"]
                ads_out.append(row)
            groups_out.append({
                "広告グループ": g["name"],
                "状態": convert.ja(convert.STATUS_JA, g["status"]),
                "表示オプション（広告グループ単位）": _extension_summary(
                    [x for x in links if x["level"] == "ad_group" and x["ad_group_id"] == g["id"]]),
                "広告": ads_out,
            })
        campaigns_out.append({
            "キャンペーン": camp["name"],
            "種類": convert.ja(convert.CHANNEL_JA, camp["channel"]),
            "状態": convert.ja(convert.STATUS_JA, camp["status"]),
            "表示オプション（キャンペーン単位）": _extension_summary(
                [x for x in links if x["level"] == "campaign" and x["campaign_id"] == camp["id"]]),
            "広告グループ": groups_out,
        })
    return {
        "対象アカウント": _label(acct),
        "期間": "指定なし（いまの設定。削除済みは除く）",
        "単位": "広告 1 本ごと。表示オプションは種類ごとの件数（付け先の階層別）",
        "件数": {
            "表示しているキャンペーン": len(campaigns_out),
            "一時停止で省いたキャンペーン": 0 if not only_enabled else sum(1 for c in all_campaigns if c["status"] != "ENABLED"),
            "表示している広告": sum(len(g["広告"]) for c in campaigns_out for g in c["広告グループ"]),
        },
        "表示オプション（アカウント単位）": _extension_summary([x for x in links if x["level"] == "account"]),
        "キャンペーン": campaigns_out,
        "見出し・説明文の出どころ": ("ここの見出し・説明文は、広告に入っている文をそのまま数えたもので、出どころでは分けていない。"
                             "Google が広告に足した文（AI Max の文の自動の作成など）を見分けるには get_generated_text を使う"),
    }


def _merge(rows: List[dict], keys: List[str]) -> List[dict]:
    """同じ組み合わせの行をまとめて、実績を足す。"""
    merged: Dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        m = merged.setdefault(k, {**{x: r[x] for x in keys}, **{x: 0 for x in convert.RAW_METRICS}})
        for x in convert.RAW_METRICS:
            m[x] += r.get(x, 0)
    return list(merged.values())


def get_generated_text(customer_id: str, start: str, end: str, top_n: int = DEFAULT_TOP_N,
                       run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    gaql.validate_period(start, end)
    top_n = convert.clamp_top_n(top_n, DEFAULT_TOP_N, MAX_TOP_N)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    g = fetch.generated(cid, start, end, run)

    def rows(items: List[dict], rename: Dict[str, str]) -> List[dict]:
        ordered = convert.sort_rows(items, "表示")[:top_n]
        return [convert.to_ja_row({rename.get(k, k): v for k, v in r.items()}) for r in ordered]

    def ja_text(t: dict) -> dict:
        keep = {k: v for k, v in t.items() if k not in ("link_source", "asset_source")}
        return {**keep, "field_type": convert.ja(FIELD_JA, t["field_type"]), "label": convert.ja(LABEL_JA, t["label"]),
                "link_source": convert.ja(ASSET_SOURCE_JA, t["link_source"]),
                "asset_source": convert.ja(ASSET_SOURCE_JA, t["asset_source"])}

    by_group: Dict[tuple, Dict[str, set]] = {}
    for t in g["texts"]:
        sets = by_group.setdefault((t["campaign"], t["ad_group"]), {"adv": set(), "gen": set()})
        sets["gen" if _is_generated(t) else "adv"].add(t["text"])
    texts = [ja_text(t) for t in g["texts"] if _is_generated(t)]
    n_adv = sum(1 for t in g["texts"] if not _is_generated(t))
    combos = _merge(g["combinations"], ["campaign", "ad_group", "headline", "landing_page"])
    by_campaign: Dict[str, Dict[str, set]] = {}
    for (camp, _), sets in by_group.items():
        cs = by_campaign.setdefault(camp, {"adv": set(), "gen": set()})
        cs["adv"] |= sets["adv"]
        cs["gen"] |= sets["gen"]
    for c in combos:
        sets = by_group.get((c["campaign"], c["ad_group"]), {"adv": set(), "gen": set()})
        c["origin"] = _headline_origin(c["headline"], sets["adv"], sets["gen"] - sets["adv"])
        if c["origin"] in (ORIGIN_OTHER, ORIGIN_UNKNOWN):  # Google が足した文は、別の広告グループの組み合わせにも出ることがある
            cs = by_campaign.get(c["campaign"], {"adv": set(), "gen": set()})
            wide = _headline_origin(c["headline"], sets["adv"] | cs["adv"], cs["gen"] - sets["adv"])
            if wide == ORIGIN_GOOGLE:
                c["origin"] = ORIGIN_GOOGLE_OTHER_GROUP
            elif wide == ORIGIN_ADVERTISER:
                c["origin"] = ORIGIN_ADVERTISER
    pages = [{**p, "source": convert.ja(PAGE_SOURCE_JA, p["source"])}
             for p in _merge(g["landing_pages"], ["campaign", "ad_group", "url", "source"])]
    auto_pages = [p for p in pages if p["source"] == PAGE_SOURCE_JA["AUTOMATIC"]]
    origins = {k: sum(1 for c in combos if c["origin"] == k)
               for k in (ORIGIN_ADVERTISER, ORIGIN_GOOGLE, ORIGIN_GOOGLE_OTHER_GROUP, ORIGIN_OTHER, ORIGIN_UNKNOWN)}
    out = {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct),
        "単位": "文 1 本ごと（広告ごと）、組み合わせ 1 つごと、着地 URL 1 本ごとの期間合計。金額は円",
        "件数": {"自動で作られた見出し・説明文": len(texts),
                "入稿した見出し・説明文（表示があったもの）": n_adv,
                "見出しと着地 URL の組み合わせ": len(combos),
                "組み合わせの見出しの作り手": {k: v for k, v in origins.items() if v},
                "着地 URL": len(pages), "うち Google が自動で選んだ着地 URL": len(auto_pages)},
    }
    if texts:
        out["自動で作られた見出し・説明文"] = rows(texts, {"campaign": "キャンペーン", "ad_group": "広告グループ",
                                                  "field_type": "種類", "label": "評価", "text": "文",
                                                  "link_source": "つながりの出どころ", "asset_source": "アセットの出どころ"})
    else:
        out["自動で作られた見出し・説明文"] = ("まだ無い（この期間に表示があった見出し・説明文のうち、Google が足したもの"
                                       "（つながりかアセットの出どころが「自動で作成」）は 0 件）")
    if combos:
        out["見出しと着地 URL の組み合わせ"] = rows(combos, {"campaign": "キャンペーン", "ad_group": "広告グループ",
                                                 "headline": "見出し", "landing_page": "着地 URL", "origin": "見出しの作り手"})
    else:
        out["見出しと着地 URL の組み合わせ"] = "まだ無い（この期間に、AI Max が組み合わせて出した記録は 0 件）"
    if pages:
        out["着地 URL"] = rows(pages, {"campaign": "キャンペーン", "ad_group": "広告グループ", "url": "URL", "source": "選ばれ方"})
    else:
        out["着地 URL"] = "まだ無い（この期間に表示が無い）"
    out["広げた着地ページと、一緒に出た文"] = _expansions(cid, start, end, run, rows)
    out["件数"]["広げた着地ページ"] = _count(out["広げた着地ページと、一緒に出た文"].get("広げた着地ページ"))
    out["件数"]["広げた着地ページと一緒に出た文"] = _count(out["広げた着地ページと、一緒に出た文"].get("一緒に出た文"))
    out["文の出どころの読み方"] = {
        "自動で作られた見出し・説明文": (
            "Google が広告に足した文。運用者が入稿した原稿ではない。広告とアセットのつながりの出どころか、"
            "アセットそのものの出どころが「自動で作成」のものを出す。Google が足した文は、アセットの出どころは"
            "「広告主」のまま、つながりの側だけに印が付くことがある"),
        "見出しと着地 URL の組み合わせ": (
            "AI Max が検索語に合わせて実際に出した見出しと着地 URL。見出しは、入稿した見出しをつないだものが大半で、"
            "Google が足した文を含むこともある。「見出しの作り手」は、同じ広告グループで期間に表示があった文と"
            "文字が一致するかで見分けたもの。同じ広告グループで見分けられないときは、同じキャンペーンのほかの広告グループの文とも比べる"
            "（Google が足した文は、足された広告グループとは別の広告グループの組み合わせにも出ることがある。2026-11-24 実物で確認）。"
            "区切りの「 | 」と「｜」は同じものとして比べる。一致しない部分がある行は、Google が作り直した文の可能性がある"),
    }
    return out


def _count(v) -> object:
    if isinstance(v, list):
        return len(v)
    return 0 if str(v).startswith("まだ無い") else "読めなかった"


def _query_part(url: str) -> str:
    return "あり" if "?" in (url or "") else "なし"


def _read_or_reason(fn):
    """まだ本物で確かめていない見方は、失敗しても理由を添えて返し、ほかの欄を止めない。"""
    try:
        return fn(), None
    except Exception as ex:  # noqa: BLE001
        reason = explain_api_error(ex) if hasattr(ex, "failure") else f"{type(ex).__name__}: {str(ex)[:200]}"
        return None, f"読めなかった（{reason}）"


def _expansions(cid: str, start: str, end: str, run: Search, rows) -> dict:
    """expanded_landing_page_view と final_url_expansion_asset_view を、それぞれ別に読む。"""
    raw_pages, err_pages = _read_or_reason(lambda: fetch.expanded_pages(cid, start, end, run))
    raw_texts, err_texts = _read_or_reason(lambda: fetch.expansion_texts(cid, start, end, run))
    if err_pages:
        pages_out = err_pages
    else:
        pages = [{**p, "query": _query_part(p["url"])} for p in _merge(raw_pages, ["campaign", "ad_group", "url"])]
        pages_out = rows(pages, {"campaign": "キャンペーン", "ad_group": "広告グループ", "url": "クリックが向かった URL",
                                 "query": "URL の ? 以降"}) if pages else "まだ無い（この期間に表示が無い）"
    if err_texts:
        texts_out = err_texts
    else:
        texts = [{**t, "field_type": convert.ja(FIELD_JA, t["field_type"])}
                 for t in _merge(raw_texts, ["campaign", "ad_group", "field_type", "text", "url"])]
        texts_out = rows(texts, {"campaign": "キャンペーン", "ad_group": "広告グループ", "field_type": "種類",
                                 "text": "文", "url": "着地 URL"}) if texts else "まだ無い（この期間に、広げた URL と一緒に出た文の記録は 0 件）"
    return {
        "広げた着地ページ": pages_out,
        "一緒に出た文": texts_out,
        "読み方": ("広げた着地ページは、実際にクリックが向かった URL ごとの実績（expanded_landing_page_view）。"
                 "「URL の ? 以降」が「なし」の行は、URL に計測の印（utm など）が付いていない可能性がある"
                 "（トラッキングテンプレートで付けている印は、ここには出ない）。"
                 "一緒に出た文は、Google が広げた着地 URL と一緒に出した見出し・説明文（final_url_expansion_asset_view。検索キャンペーンだけ）"),
    }


def audit_landing_page(url: str) -> dict:
    result = lp.audit(url)
    return {
        "対象アカウント": "なし（LP の監査）",
        "期間": "いまの 1 回の計測",
        "単位": "ページ 1 本。時間は秒",
        **result,
        "見えない範囲": "ページの要素とタグは、HTML を 1 回取得して読んだ範囲。あとから JavaScript で入るタグは見えない。",
    }


def _lp_urls(snap: dict) -> List[str]:
    """点検で測る LP。有効なキャンペーン・広告グループの、有効な広告の最終 URL（計測用の ? 以降は外す）。"""
    urls: List[str] = []
    for camp in checks.enabled_campaigns(snap):
        group_ids = {g["id"] for g in checks.enabled_ad_groups(snap, camp["id"])}
        for ad in snap["ads"]:
            if ad["ad_group_id"] in group_ids and ad["status"] == "ENABLED":
                for u in ad["final_urls"]:
                    p = _urlparse.urlsplit(u)
                    clean = _urlparse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
                    if clean not in urls:
                        urls.append(clean)
    return urls


def build_snapshot(cid: str, start: str, end: str, run: Search) -> dict:
    return {
        "campaigns": fetch.campaigns(cid, start, end, run),
        "conversion_setup": fetch.conversion_setup(cid, run),
        "ad_groups": fetch.ad_groups(cid, run),
        "ads": fetch.ads(cid, run),
        "asset_links": fetch.asset_links(cid, run),
        "keywords": fetch.keywords(cid, run),
        "negatives": fetch.negatives(cid, run),
        "search_terms": fetch.search_terms(cid, start, end, run),
        "lp_audits": None,
    }


def run_checks(customer_id: str, start: str, end: str, include_lp: bool = True,
               run: Optional[Search] = None, audit: Optional[Callable] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    gaql.validate_period(start, end)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    snap = build_snapshot(cid, start, end, run)
    notes = []
    if include_lp:
        urls = _lp_urls(snap)
        audit = audit or (lambda u: lp.audit(u, max_age_hours=LP_REUSE_HOURS))
        snap["lp_audits"] = []
        for u in urls[:LP_MAX_URLS]:
            try:
                snap["lp_audits"].append(audit(u))
            except ValueError as ex:
                snap["lp_audits"].append({"URL": u, "表示速度（スマホ）": {"測れたか": False, "理由": str(ex)}})
        if len(urls) > LP_MAX_URLS:
            notes.append(f"LP は {len(urls)} 本のうち先頭の {LP_MAX_URLS} 本だけ測った。残りは audit_landing_page で 1 本ずつ測る。")
    results = checks.run_all(snap)
    enabled = checks.enabled_campaigns(snap)
    path = out_dir() / f"checks_{cid}_{start}_{end}.json"
    path.write_text(_json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    out = {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct),
        "単位": "点検の所見 1 件ごと。金額は円、割合は %",
        "点検した範囲": {
            "有効なキャンペーン": [c["name"] for c in enabled],
            "一時停止で対象外にしたキャンペーンの数": sum(1 for c in snap["campaigns"] if c["status"] != "ENABLED"),
        },
        **checks.summarize(results),
        "全部の結果の保存先": str(path),
    }
    if notes:
        out["注記"] = notes
    return out


# ================= 段 3 =================
from datetime import date as _date, timedelta as _timedelta  # noqa: E402

from . import forecast, intel  # noqa: E402
from .client import api_version, keyword_history  # noqa: E402,F401

HISTORY_DAYS = 180


def _keyword_history(cid: str, keywords: List[str], geo_id: int, language_id: int) -> List[dict]:
    global _client
    if _client is None:
        _client = load_client()
    return keyword_history(_client, cid, keywords, geo_id, language_id)


def _planner_reason(ex: Exception) -> str:
    text = explain_api_error(ex)
    if "explorer access" in text:
        return ("キーワードプランナーの API は、いまの API の利用権限（explorer access）では使えないと Google が返した。"
                "basic access を申請して通れば使える。それまでは、キーワードプランナーの画面で見た月間検索数を "
                "monthly_searches で渡すと計算できる。")
    return f"キーワードプランナーから検索数を取れなかった: {explain_api_error(ex, keyword_planner=True)}"


def _keyword_list(keywords) -> List[str]:
    if isinstance(keywords, str):
        keywords = re_split(keywords)
    seen, out = set(), []
    for k in keywords or []:
        k = str(k).strip()
        n = forecast.normalize(k)
        if n and n not in seen:
            seen.add(n)
            out.append(k)
    if not out:
        raise ValueError("キーワードを 1 個以上渡す")
    if len(out) > forecast.MAX_KEYWORDS:
        raise ValueError(f"キーワードは 1 回に {forecast.MAX_KEYWORDS} 個まで（受け取った数: {len(out)}）")
    return out


def re_split(text: str) -> List[str]:
    import re

    return [x for x in re.split(r"[,、\n]", text) if x.strip()]


def forecast_volume(customer_id: str, keywords, daily_budget_yen: Optional[float] = None,
                    monthly_searches: Optional[Dict[str, float]] = None, history_start: str = "",
                    history_end: str = "", record: bool = True, run: Optional[Search] = None,
                    planner: Optional[Callable] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    planner = planner or _keyword_history
    cid = gaql.normalize_customer_id(customer_id)
    words = _keyword_list(keywords)
    if daily_budget_yen is not None:
        try:
            daily_budget_yen = float(daily_budget_yen)
        except (TypeError, ValueError):
            raise ValueError("1 日の予算は円の数字で渡す") from None
        if daily_budget_yen <= 0:
            raise ValueError("1 日の予算は 0 より大きい円の数字で渡す")
    today = today or _date.today()
    end = history_end or (today - _timedelta(days=1)).isoformat()
    start = history_start or (_date.fromisoformat(end) - _timedelta(days=HISTORY_DAYS - 1)).isoformat()
    gaql.validate_period(start, end)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    defaults = _defaults(cid, acct, run)
    country, language = defaults.get("既定の国"), defaults.get("既定の言語")

    # 自分の実績（キーワードごと、アカウント全体）
    by_keyword: Dict[str, List[dict]] = {}
    for r in fetch.keyword_history_rows(cid, start, end, run):
        by_keyword.setdefault(forecast.normalize(r["text"]), []).append(r)
    search_campaigns = [c for c in fetch.campaigns(cid, start, end, run) if c["channel"] == "SEARCH"]
    account = forecast.combine(search_campaigns)

    # 検索数（渡された値を先に使い、残りをキーワードプランナーに聞く）
    given = {forecast.normalize(k): v for k, v in (monthly_searches or {}).items()}
    for k, v in given.items():
        if not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"月間検索数は 0 以上の数字で渡す（{k}: {v!r}）")
    volumes: Dict[str, dict] = {}
    planner_reason = None
    ask = [k for k in words if forecast.normalize(k) not in given]
    if ask:
        if not country or not language:
            planner_reason = "国と言語の既定値を決められないので、キーワードプランナーに聞いていない"
        else:
            try:
                for item in planner(cid, ask, country["地域 ID"], language["言語 ID"]):
                    for name in [item["text"], *item.get("close_variants", [])]:
                        volumes[forecast.normalize(name)] = item
            except (ValueError, SetupError):
                raise
            except Exception as ex:
                if getattr(ex, "failure", None) is None:
                    raise
                planner_reason = _planner_reason(ex)

    rows, projections, raws = [], [], []
    budget_month = daily_budget_yen * forecast.DAYS_PER_MONTH if daily_budget_yen else None
    for k in words:
        n = forecast.normalize(k)
        notes: List[str] = []
        if n in given:
            vol = forecast.volume_range(int(given[n]), None)
            source = "渡された値"
            extra = {}
        elif n in volumes:
            item = volumes[n]
            vol = forecast.volume_range(item["avg_monthly_searches"], item.get("monthly"))
            source = "キーワードプランナー（Google 広告 API）"
            low, high = item.get("low_bid_micros") or 0, item.get("high_bid_micros") or 0
            if low or high:
                extra = {"ページ上部に出すための入札の目安（円）": [convert.micros_to_yen(low), convert.micros_to_yen(high)]}
            else:  # Google が値を返さない語がある（0 円という意味ではない）
                extra = {"ページ上部に出すための入札の目安（円）": None}
                notes.append("ページ上部に出すための入札の目安は、キーワードプランナーが値を返さなかった（0 円という意味ではない）。")
            if vol["recent3"] is not None and vol["mid"] > 0 and not 0.5 <= vol["recent3"] / vol["mid"] <= 2:
                notes.append("直近 3 か月の平均が年平均の半分未満か 2 倍超。年平均をそのまま信じない。")
        else:
            vol, source, extra = None, "取れなかった", {}
        rates = forecast.pick_rates(forecast.combine(by_keyword[n]) if n in by_keyword else None, account)
        raw = forecast.raw_projection(vol, rates)
        proj = forecast.add_up([raw], budget_month)
        used_account = [x for x in ("share", "ctr", "cpc") if rates.get(x) and rates[x]["source"] == forecast.SOURCE_ACCOUNT]
        if used_account:
            notes.append("このキーワードの実績が少ないので、アカウント全体の値で代用した項目がある。そのぶん幅が広い。")
        row = {
            "キーワード": k,
            "月間検索数": round(vol["mid"]) if vol else None,
            "直近 3 か月の平均": vol["recent3"] if vol else None,
            "検索数の出どころ": source,
            **extra,
            "使った割合・率・単価": forecast.rate_view(rates),
            "見込み（月）": proj,
        }
        if proj is None:
            row["見込みを出せない理由"] = (
                planner_reason or "検索数が返らなかった" if vol is None
                else "この期間、検索キャンペーンの実績が無いので、割合・率・単価を決められない"
            )
        if notes:
            row["注記"] = notes
        rows.append(row)
        projections.append(proj)
        raws.append(raw)

    total = forecast.add_up(raws, budget_month)
    total_capped = bool(total and total.pop("予算で頭打ち"))
    notes = forecast.script_notes(words, (language or {}).get("言語コード"))
    if account["impressions"] < forecast.MIN_IMPRESSIONS_OWN or account["clicks"] < forecast.MIN_CLICKS_OWN:
        notes.append(f"アカウント全体の実績も少ない（表示 {account['impressions']}・クリック {account['clicks']}）。"
                     "見込みの幅はとても広く、目安にとどまる。")
    if planner_reason:
        notes.append(planner_reason)
    if total_capped:
        notes.append("予算が無ければもっと出る見込みだが、1 か月の予算で頭打ちになる。"
                     "このとき表示とクリックの幅は、単価が高い場合（低）と安い場合（高）の差で決まる。")
    history = f"{start}〜{end}"
    out = {
        "対象アカウント": _label(acct),
        "期間": f"見込みは 1 か月（{forecast.DAYS_PER_MONTH} 日）あたり。割合・率・単価は {history} の実績から",
        "単位": "キーワード 1 個ごとの月の見込み（低・中・高）。金額は円",
        "これは何か": "道具が計算した見込み（予測）。実績ではない。",
        "地域と言語": {"国": country, "言語": language, "根拠": defaults.get("既定値の根拠")},
        "1 日の予算（円）": daily_budget_yen,
        "合計の見込み（月）": total,
        "行": rows,
        "式": forecast.FORMULA,
        "前提": [
            "表示される割合は、広告が出る資格のあった検索のうち実際に表示できた割合（自分の実績）。月間検索数の全部が資格のある検索だとみなして掛けている。",
            "月間検索数は、その語と近い言い換えの検索数。部分一致で広がる検索は入っていない。",
            "幅: 割合と率は件数から出した 95% の幅、単価はクリック数に応じた置き値（±15〜50%）、検索数は年平均と直近 3 か月の平均。"
            "低は全部の低、高は全部の高を掛けているので広い。",
            f"そのキーワード自身の値を使うのは、表示 {forecast.MIN_IMPRESSIONS_OWN} 回以上（割合・率）、"
            f"クリック {forecast.MIN_CLICKS_OWN} 件以上（単価）のとき。足りなければアカウント全体の値で代用する。",
            "Google 広告は、表示される割合が 10% 未満のとき 9.99% と返す。",
        ],
    }
    if notes:
        out["注記"] = notes
    if record and any(projections):
        out["記録"] = forecast.record(cid, rows, total, history, daily_budget_yen)
    elif record:
        out["記録"] = "見込みが 1 つも出なかったので、記録していない"
    return out


def forecast_vs_actual(customer_id: str, start: str = "", end: str = "", forecast_id: str = "",
                       run: Optional[Search] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    records = forecast.read_records(cid)
    if not start or not end:
        return {
            "対象アカウント": cid,
            "期間": "指定なし（記録の一覧）",
            "単位": "見込みの記録 1 件ごと（新しい順）",
            "件数": len(records),
            "行": [{"id": r["id"], "日時": r["日時"], "キーワード": [k["キーワード"] for k in r.get("キーワード", [])],
                   "月の費用の見込み（中、円）": ((r.get("合計の見込み（月）") or {}).get("費用（円）") or {}).get("中")}
                  for r in reversed(records)][:20],
            "保存先": str(forecast.log_path()),
            "使い方": "実績と並べるには、start と end（YYYY-MM-DD）を渡す。forecast_id を省くといちばん新しい記録を使う。",
        }
    gaql.validate_period(start, end)
    if not records:
        raise ValueError("このアカウントの見込みの記録がまだ無い。先に forecast_volume で見込みを出す")
    if forecast_id:
        picked = [r for r in records if r["id"] == forecast_id]
        if not picked:
            raise ValueError(f"見込みの記録 {forecast_id!r} が見つからない")
        entry = picked[0]
    else:
        entry = records[-1]
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    actual: Dict[str, dict] = {}
    for r in fetch.keyword_history_rows(cid, start, end, run):
        a = actual.setdefault(forecast.normalize(r["text"]), {"impressions": 0, "clicks": 0, "cost_micros": 0})
        for key in a:
            a[key] += r[key]
    result = forecast.compare(entry, actual, start, end)
    return {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct) + f"・{result['日数']} 日",
        "単位": "キーワード 1 個ごと。見込みは月の値を期間の日数に直したもの。金額は円",
        "使った見込み": {"id": entry["id"], "出した日時": entry["日時"], "実績を取った期間": entry.get("実績を取った期間")},
        "合計": result["合計"],
        "行": result["行"],
        "読み方": "「実績 ÷ 見込みの中」が 1 に近いほど当たり。幅から外れが続く項目は、その割合・率・単価の置き方を見直す。"
               "期間の中に配信していない日があると、実績は見込みより少なく出る（見込みは毎日配信した場合の値）。",
    }


def get_new_intel(since: str = "", top_n: int = DEFAULT_TOP_N) -> dict:
    return intel.get_new_intel(since or None, top_n)


def search_cards(query: str = "", status: str = "", tier: str = "", top_n: int = DEFAULT_TOP_N) -> dict:
    return intel.search_cards(query, status or None, tier or None, top_n)


def match_card_to_account(card_id: str, customer_id: str, run: Optional[Search] = None,
                          today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    card = intel.find_card(card_id)
    acct = _account(cid, run)
    state = fetch.account_state(cid, run)
    today = today or _date.today()
    start = (today - _timedelta(days=30)).isoformat()
    end = (today - _timedelta(days=1)).isoformat()
    state["conversions_30d"] = round(sum(c["conversions"] for c in fetch.campaigns(cid, start, end, run)), 2)
    return intel.match_card(card, state, intel.tool_facts(), _label(acct), today)


# ================= 段 3.6: 変更の履歴・変更の前後・いつもの水準からの外れ（どれも読むだけ） =================
from datetime import datetime as _datetime  # noqa: E402

from . import kb, monitor  # noqa: E402

CHANGE_FETCH_LIMIT = 1000   # 1 回に読む変更の行数（API の上限 10,000 より小さく置く。個人のアカウントには足りる）
EFFECT_MAX_DAYS = 90
EFFECT_MAX_TARGETS = 20
LOOKBACK_MIN, LOOKBACK_MAX = 7, 90


def _today(acct: dict) -> _date:
    """アカウントのタイムゾーンでの今日。"""
    try:
        from zoneinfo import ZoneInfo

        return _datetime.now(ZoneInfo(acct["タイムゾーン"])).date()
    except Exception:
        return _date.today()


def _int_in(label: str, v, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}は整数で指定する（受け取った値: {v!r}）") from None
    if not lo <= n <= hi:
        raise ValueError(f"{label}は {lo}〜{hi} の整数で指定する（受け取った値: {v}）")
    return n


def get_change_history(customer_id: str, start: str = "", end: str = "", top_n: int = DEFAULT_TOP_N,
                       run: Optional[Search] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    for v in (start, end):
        if v:
            gaql.validate_period(v, v)
    top_n = convert.clamp_top_n(top_n, DEFAULT_TOP_N, MAX_TOP_N)
    acct = _account(cid, run)
    today = today or _today(acct)
    earliest = today - _timedelta(days=gaql.CHANGE_EVENT_DAYS - 1)
    start = start or earliest.isoformat()
    end = end or today.isoformat()
    gaql.validate_period(start, end)
    if _date.fromisoformat(start) < earliest:
        raise ValueError(
            f"API の変更の履歴（change_event）は過去 {gaql.CHANGE_EVENT_DAYS} 日までしか読めない。"
            f"開始日は {earliest.isoformat()} 以降にする（受け取った値: {start}）。"
            "それより前は、管理画面の［変更履歴］（2 年分）で見る。")
    if _date.fromisoformat(end) > today:
        raise ValueError(f"終了日 {end} が今日（{today.isoformat()}、{acct['タイムゾーン']}）より後になっている")
    end_next = (_date.fromisoformat(end) + _timedelta(days=1)).isoformat()
    events = [e for e in fetch.change_events(cid, start, end_next, CHANGE_FETCH_LIMIT, run) if e["time"][:10] <= end]
    names = fetch.campaign_names(cid, run) if events else {}
    rows = monitor.change_rows(events, names)
    saved = None
    if rows:
        flat = [{k: (_json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                 for k, v in r.items() if k != "client_type"} for r in rows]
        saved = convert.write_csv(out_dir() / f"change_history_{cid}_{start}_{end}.csv", flat)
    watch = [r for r in rows if r["client_type"] in monitor.AUTO_CLIENTS]
    for r in rows:
        r.pop("client_type")
    notes = [
        f"API の変更の履歴は過去 {gaql.CHANGE_EVENT_DAYS} 日まで。問い合わせには行数の上限（LIMIT）が必須で、最大 10,000 行。"
        f"この道具は新しい順に {CHANGE_FETCH_LIMIT:,} 行まで読む。",
        "変更が API に出るまで最大 3 分かかる。Google 広告エディタでの変更は API に返らないので、管理画面の［変更履歴］で見る。",
        "変更した人のメールアドレスは読まない（取らない）。「経路」は、どの道具から変えたか（管理画面・自動ルール・最適化案の自動適用など）。",
        "この道具（ochinana-ads）は読むだけなので、ここに出る変更はこの道具が行ったものではない。",
    ]
    if len(events) >= CHANGE_FETCH_LIMIT:
        notes.append(f"{CHANGE_FETCH_LIMIT:,} 行で打ち切った。古い分は、期間を短くして読み直す。")
    return {
        "対象アカウント": _label(acct),
        "期間": _period(start, end, acct),
        "単位": "変更 1 件ごと（新しい順）。金額は円",
        "件数": {
            "変更": len(rows),
            "表示している件数": min(top_n, len(rows)),
            "経路別": convert.count_by(rows, "経路"),
            "種類別": convert.count_by(rows, "種類"),
            "操作別": convert.count_by(rows, "操作"),
            "人が画面で変えたとは限らない経路": len(watch),
        },
        "並び順": "日時の新しい順",
        "行": rows[:top_n],
        "見ておきたい変更（自動適用・自動ルール・スクリプト・API など）": watch[:top_n],
        "全件の保存先": saved,
        "注記": notes,
        "根拠": [kb.cite("27", "27-28"), kb.cite("27", "27-29"), kb.cite("27", "27-26"),
               kb.cite_line("27", "点検の型: 週 1 回")],
    }


def _group(rows: List[dict], level: str) -> Dict[tuple, dict]:
    """日別の行を、対象（キャンペーン・広告グループ・アカウント全体）ごとに日付で束ねる。"""
    out: Dict[tuple, dict] = {}
    for r in rows:
        if level == "account":
            key, label = ("account",), "アカウント全体"
        elif level == "ad_group":
            key, label = (r["campaign_id"], r["ad_group_id"]), f"{r['campaign']} / {r['ad_group']}"
        else:
            key, label = (r["campaign_id"],), r["campaign"]
        g = out.setdefault(key, {"label": label, "info": {k: r.get(k) for k in ("status", "channel", "budget_micros")},
                                 "days": {}})
        day = g["days"].setdefault(r["date"], {k: 0 for k in convert.RAW_METRICS})
        for k in convert.RAW_METRICS:
            day[k] += r.get(k, 0)
    return out


def measure_effect(customer_id: str, change_date: str, days_before: int = 14, days_after: int = 14,
                   level: str = "campaign", campaign_id: str = "", lag_days: int = monitor.LAG_DAYS,
                   skip_days: int = 0, run: Optional[Search] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    gaql.validate_period(change_date, change_date)
    level = gaql.normalize_daily_level(level, ("campaign", "ad_group", "account"))
    campaign_id = gaql.normalize_campaign_id(campaign_id)
    nb = _int_in("days_before（前の日数）", days_before, 1, EFFECT_MAX_DAYS)
    na = _int_in("days_after（後の日数）", days_after, 1, EFFECT_MAX_DAYS)
    lag = _int_in("lag_days（成果の遅れとして外す日数）", lag_days, 0, 30)
    skip = _int_in("skip_days（変更直後に外す日数）", skip_days, 0, 30)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    today = today or _today(acct)
    yesterday = today - _timedelta(days=1)
    d0 = _date.fromisoformat(change_date)
    if d0 >= today:
        raise ValueError(f"変更日 {change_date} が今日（{today.isoformat()}）以降。前後を比べられるのは過去の変更だけ")
    before = monitor.date_range(d0 - _timedelta(days=nb), d0 - _timedelta(days=1))
    after_start = d0 + _timedelta(days=1 + skip)
    after_end = min(d0 + _timedelta(days=skip + na), yesterday)
    if after_start > after_end:
        raise ValueError(f"変更日の翌日{f'から {skip} 日を外した日' if skip else ''}以降の実績がまだ無い"
                         f"（後の期間が {after_start.isoformat()} から始まり、昨日 {yesterday.isoformat()} を越える）")
    after = monitor.date_range(after_start, after_end)
    confirmed_until = today - _timedelta(days=lag + 1) if lag else yesterday
    rows = fetch.daily(cid, "ad_group" if level == "ad_group" else "campaign", before[0].isoformat(),
                       after[-1].isoformat(), run, campaign_id)
    groups = _group(rows, level)
    total_days: Dict[str, dict] = {}
    for g in groups.values():
        for d, m in g["days"].items():
            t = total_days.setdefault(d, {k: 0 for k in convert.RAW_METRICS})
            for k in convert.RAW_METRICS:
                t[k] += m[k]
    total = monitor.before_after(total_days, before, after, confirmed_until)
    total.pop("_cost")
    blocks = []
    if level != "account":
        for g in groups.values():
            b = monitor.before_after(g["days"], before, after, confirmed_until)
            blocks.append((b.pop("_cost"), {"対象": g["label"], **b}))
        blocks.sort(key=lambda x: -x[0])
    mix, even = monitor.weekday_mix(before, after)
    notes = ["これは変更の「前後の差」で、変更が原因だとは言えない。同じ時期のほかの変更・季節・競合・需要の動きも入る。"
             "ほかの変更は get_change_history（過去 30 日）で確かめる。比べる相手（変更しなかったキャンペーン）の同じ期間の変化も見る。"]
    if len(after) < na:
        notes.append(f"後の期間は昨日までしか無いので、{na} 日のうち {len(after)} 日で比べた。")
    lagged = [d for d in after if d > confirmed_until]
    if lagged:
        notes.append(f"成果は遅れて入る（クリックの日に後から足される）。{confirmed_until.isoformat()} より後の {len(lagged)} 日は未確定とみなし、"
                     f"コンバージョン数・率・単価から外した（lag_days={lag}。成果の 90% が出そろう日数が分かればその値にする）。"
                     "表示・クリック・費用はその日のうちにほぼ確定するので外していない。")
    if not skip:
        notes.append("入札戦略・目標・予算を変えたときは、学習で落ち着くまでの期間（公式に「約 2 週間」）を評価に入れないほうがよい。"
                     "skip_days で変更直後の日を外せる。")
    if not even:
        notes.append("前と後で曜日の構成が違う（日数を 7 の倍数にすると、そろう）。曜日で数字が動く広告では、それだけで差が出る。")
    small = [r["指標"] for r in total["行"] if r.get("判定") == "判定できない"]
    if small:
        notes.append(f"判定できない指標がある（{'、'.join(small)}）。件数が足りないだけで、「変化が無かった」「効かなかった」という意味ではない。")
    notes.append("件数の幅は、前の期間の水準をそのまま正しいとみなして出している（前の期間自体の揺れは入れていない）。"
                 "実際の広告の数字は曜日・季節でこの幅より大きく揺れるので、幅の外でも「確かな差」とは言い切らない。")
    out = {
        "対象アカウント": _label(acct),
        "期間": (f"前: {before[0]}〜{before[-1]}（{len(before)} 日）／後: {after[0]}〜{after[-1]}（{len(after)} 日）。"
               f"変更日 {change_date} は比べに入れない（{acct['タイムゾーン']} の日付）"),
        "単位": f"{'アカウント全体' if level == 'account' else gaql.LEVEL_JA[level] + 'ごとと合計'}の前後の比べ。金額は円、率は %",
        "これは何か": "変更の前後の差を、偶然の範囲と並べたもの。因果の判定ではない。",
        "条件": {"変更日": change_date, "前の日数": nb, "後の日数": na, "変更直後に外した日数": skip,
               "成果の遅れとして外した日数": lag, "成果を確定とみなした最後の日": confirmed_until.isoformat(),
               "キャンペーン ID": campaign_id or "指定なし（全部）"},
        "曜日の数": mix,
        "合計": total["行"],
    }
    if level != "account":
        out["対象の数"] = {"実績のある対象": len(blocks), "表示している数": min(EFFECT_MAX_TARGETS, len(blocks))}
        out[f"{gaql.LEVEL_JA[level]}ごと"] = [b for _, b in blocks[:EFFECT_MAX_TARGETS]]
    out["判定の範囲"] = [
        "件数（表示・クリック・成果）: 前の 1 日あたりの水準が続いたときに後の期間で偶然に出る範囲（ポアソン分布。平均 1,000 件以上は正規近似）に入るか。",
        "率（クリック率・コンバージョン率）: 2 つの率の検定（期待される件数が 5 未満ならフィッシャーの正確検定）、有意水準 5%。",
        "単価（クリック単価・コンバージョン単価）: 分母の件数の 95% の幅から単価の幅を出し、前後の幅が重なるか。",
        f"前後合わせて {monitor.MIN_EVENTS} 件に届かない指標は「判定できない」（知識ベース 36 の 5-6 の目安。公式の数字ではない）。",
        "コンバージョン数は小数のことがあるので、検定では四捨五入した件数を使う。",
    ]
    out["注記"] = notes
    out["根拠"] = monitor.effect_basis()
    return out


def get_anomalies(customer_id: str, end: str = "", lookback_days: int = 28, level: str = "campaign",
                  run: Optional[Search] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    if end:
        gaql.validate_period(end, end)
    level = gaql.normalize_daily_level(level, ("campaign", "account"))
    lookback = _int_in("lookback_days（比べる日数）", lookback_days, LOOKBACK_MIN, LOOKBACK_MAX)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    today = today or _today(acct)
    end_d = _date.fromisoformat(end) if end else today - _timedelta(days=1)
    if end_d >= today:
        raise ValueError(f"見る日 {end_d.isoformat()} が今日以降。今日の分は途中なので、昨日（{(today - _timedelta(days=1)).isoformat()}）までを指定する")
    baseline = monitor.date_range(end_d - _timedelta(days=lookback), end_d - _timedelta(days=1))
    rows = fetch.daily(cid, "campaign", baseline[0].isoformat(), end_d.isoformat(), run)
    campaigns = _group(rows, "campaign")
    results: List[dict] = []
    skipped = []
    table = []
    if level == "account":
        days: Dict[str, dict] = {}
        for g in campaigns.values():
            for d, m in g["days"].items():
                t = days.setdefault(d, {k: 0 for k in convert.RAW_METRICS})
                for k in convert.RAW_METRICS:
                    t[k] += m[k]
        targets = [("アカウント全体", {"status": "ENABLED"}, days, False)]
    else:
        targets = []
        for g in sorted(campaigns.values(), key=lambda g: g["label"]):
            if g["info"].get("status") != "ENABLED":
                skipped.append(g["label"])
                continue
            targets.append((g["label"], g["info"], g["days"], True))
    for label, info, days, budget_checks in targets:
        results.extend(monitor.detect(label, info, days, end_d, baseline, budget_checks))
        y = days.get(end_d.isoformat()) or {k: 0 for k in convert.RAW_METRICS}
        row = {"対象": label, **{k: v for k, v in convert.metrics_ja(y).items() if k != "コンバージョン値（円）"}}
        if info.get("budget_micros"):
            row["1 日の予算（円、いまの額）"] = convert.micros_to_yen(info["budget_micros"])
        table.append(row)
    summary = monitor.summarize(results)
    notes = [
        "比べる相手は、同じ曜日が 3 日以上あれば同じ曜日の平均、無ければ前の日数ぶんの平均。祝日・セールの日は外していない。",
        "偶然の範囲はポアソン分布（いちばん単純なモデル）で出している。実際の広告の数字はこれより大きく揺れるので、外れても誤報のことがある。",
        "向きは、費用は上振れ、表示・クリック・成果は下振れだけを見る（成果の上振れは 3 倍以上だけ）。",
        "予算はいまの額で比べている。期間の途中で予算を変えた・共有予算のときは、D2・D3 の倍率がずれる。",
        "成果は遅れて入るので、直近の日の成果が少ないのは遅れのこともある（ラストクリック以外のモデルで反映に約 15 時間。90 の 5-3）。",
        "所見の「確かめること」は知識ベース 36 の日次の点検表から引いたもの。止める・戻すなどの変更は、運用者が決める。",
    ]
    if not targets:
        notes.append("有効なキャンペーンが無いので、見る対象が無い。")
    if skipped:
        notes.append(f"有効でない（一時停止など）キャンペーンは対象外にした: {'、'.join(skipped[:10])}{' ほか' if len(skipped) > 10 else ''}")
    return {
        "対象アカウント": _label(acct),
        "期間": (f"見た日: {end_d.isoformat()}／比べた相手: {baseline[0]}〜{baseline[-1]}（{len(baseline)} 日）"
               f"（{acct['タイムゾーン']} の日付）"),
        "単位": f"{'アカウント全体' if level == 'account' else 'キャンペーンごと'}の、見た日の外れ。金額は円",
        **summary,
        "見た日の数字": table,
        "誰の意見": monitor.OPINION,
        "判定の範囲": [
            "D1 配信の停止: 有効なのに表示 0。いつもの水準で 0 が偶然の範囲の外なら所見。",
            "表示・クリックの下振れ: いつもの水準のポアソンの範囲（平均 1,000 以上は正規近似）より下。2 日続けば中、1 日だけなら小。いつもの水準が 1 日 5 未満なら判定できない。",
            "クリック率の下振れ: 2 つの率の検定で p 値 1% 未満かつ低いとき（誤報を減らすため厳しめ）。",
            f"D2 費用の使いすぎ: 日予算の {monitor.OVER_BUDGET} 倍超が 2 日続く（36 の D2）。1 日だけ 2 倍近くは仕様の範囲なので所見にしない。",
            f"D3 費用の使えなさ: 日予算の {monitor.UNDER_BUDGET} 倍未満が 2 日続く。いつもの費用がもともと低いキャンペーンは判定できない。",
            f"D4 成果の急変: 0 件が 3 ÷（1 日の平均）日以上続く、または平均の {monitor.CONV_SPIKE} 倍以上。比べる期間の成果が {monitor.MIN_EVENTS} 件未満なら判定できない。",
            "倍率と件数の線は、知識ベース 36 の【このメモ】の目安と道具の置き値で、公式の数字ではない。",
        ],
        "注記": notes,
        "根拠": monitor.anomaly_basis(),
    }


# ---------------- 段 4: レポートの数字の束と、文章の数字の突き合わせ ----------------

from . import numcheck, report  # noqa: E402


def _api_rows(rows: List[dict], level: str) -> List[dict]:
    """fetch.daily の行を、レポートの共通の形に直す（どの入口でもこの形にそろえる）。"""
    out = []
    for r in rows:
        if level == "ad_group":
            key, name = str(r["ad_group_id"]), f"{r['campaign']} / {r['ad_group']}"
        else:
            key, name = str(r["campaign_id"]), r["campaign"]
        out.append({"date": r["date"], "key": key, "name": name, "media": "Google 広告",
                    "impressions": r["impressions"], "clicks": r["clicks"], "cost": r["cost_micros"] / 1_000_000,
                    "conversions": r["conversions"], "value": r["conversions_value"]})
    return out


def _opt_num(label: str, v) -> Optional[float]:
    if v in (None, "", 0):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}は数字で渡す（受け取った値: {v!r}）") from None
    if x <= 0:
        raise ValueError(f"{label}は 0 より大きい数字で渡す")
    return x


def report_numbers(customer_id: str, start: str, end: str, compare: str = "previous", compare_start: str = "",
                   compare_end: str = "", level: str = "campaign", campaign_id: str = "",
                   target_cpa_yen=None, target_roas=None, plan_cost_yen=None, plan_conversions=None,
                   plan_start: str = "", plan_end: str = "", lag_days: int = report.LAG_DAYS,
                   max_rows: int = report.MAX_BREAKDOWN, include_daily: bool = True,
                   run: Optional[Search] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    gaql.validate_period(start, end)
    level = gaql.normalize_daily_level(level, ("campaign", "ad_group"))
    campaign_id = gaql.normalize_campaign_id(campaign_id)
    lag = _int_in("lag_days（成果の遅れとして見る日数）", lag_days, 0, 30)
    max_rows = _int_in("max_rows（内訳の行数）", max_rows, 1, 100)
    t_cpa = _opt_num("target_cpa_yen（目標のコンバージョン単価）", target_cpa_yen)
    t_roas = _opt_num("target_roas（目標の ROAS、%）", target_roas)
    p_cost = _opt_num("plan_cost_yen（計画の費用）", plan_cost_yen)
    p_conv = _opt_num("plan_conversions（計画のコンバージョン）", plan_conversions)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    today = today or _today(acct)
    s, e = _date.fromisoformat(start), _date.fromisoformat(end)
    if e >= today:
        raise ValueError(f"終了日は昨日（{(today - _timedelta(days=1)).isoformat()}）まで。今日の分はまだ途中")
    if report.period_days(s, e) > 366:
        raise ValueError("期間は 366 日まで")
    if compare_start or compare_end:
        gaql.validate_period(compare_start, compare_end)
        cs, ce = _date.fromisoformat(compare_start), _date.fromisoformat(compare_end)
        label = "日付で指定"
    else:
        cs, ce, label = report.compare_period(s, e, str(compare).strip())
    if ce >= s and cs <= e:
        raise ValueError("比較期間が今の期間と重なっている")
    fetch_level = "ad_group" if level == "ad_group" else "campaign"
    cur = _api_rows(fetch.daily(cid, fetch_level, s.isoformat(), e.isoformat(), run, campaign_id), level)
    prev = _api_rows(fetch.daily(cid, fetch_level, cs.isoformat(), ce.isoformat(), run, campaign_id), level)
    plan = None
    if p_cost or p_conv:
        ps = _date.fromisoformat(plan_start) if plan_start else s
        pe = _date.fromisoformat(plan_end) if plan_end else e
        gaql.validate_period(ps.isoformat(), pe.isoformat())
        if ps > e:
            raise ValueError("計画の期間が、今の期間の終了日より後に始まっている")
        upto = min(pe, e)
        rows = cur if (ps, upto) == (s, e) else _api_rows(
            fetch.daily(cid, fetch_level, ps.isoformat(), upto.isoformat(), run, campaign_id), level)
        tot = report.sums(rows)
        plan = {"cost": p_cost, "conversions": p_conv, "start": ps.isoformat(), "end": pe.isoformat(),
                "days": report.period_days(ps, pe), "elapsed_days": report.period_days(ps, upto),
                "actual": {"cost": tot["cost"], "conversions": tot["conversions"]}}
    head = {
        "対象アカウント": _label(acct),
        "期間": f"今: {s}〜{e}（{report.period_days(s, e)} 日）／比較: {cs}〜{ce}（{report.period_days(cs, ce)} 日、{label}）。"
              f"{acct['タイムゾーン']} の日付",
        "単位": f"金額は円、率は %、率の差は pt。内訳は{gaql.LEVEL_JA[level]}ごと",
    }
    if campaign_id:
        head["絞り込み"] = f"キャンペーン ID {campaign_id}"
    bundle, nums = report.build(cur, prev, start=s, end=e, cstart=cs, cend=ce, compare_label=label, today=today,
                                lag_days=lag, target_cpa=t_cpa, target_roas=t_roas, plan=plan, max_rows=max_rows,
                                source="Google 広告の API（読むだけ）", head=head, include_daily=include_daily)
    path = numcheck.save_bundle(bundle, nums, rows=sorted(prev + cur, key=lambda r: (r["date"], r["name"])))
    bundle["保存先"] = str(path)
    return bundle


from . import csvin  # noqa: E402


def report_numbers_csv(csv_path: str, start: str, end: str, compare: str = "previous", compare_start: str = "",
                       compare_end: str = "", media: str = "", columns=None, target_cpa_yen=None, target_roas=None,
                       lag_days: int = report.LAG_DAYS, today: Optional[_date] = None) -> dict:
    """媒体の管理画面から書き出した CSV（手元のファイル）から、report_numbers と同じ束を作る。API は呼ばない。"""
    gaql.validate_period(start, end)
    lag = _int_in("lag_days（成果の遅れとして見る日数）", lag_days, 0, 30)
    t_cpa = _opt_num("target_cpa_yen（目標のコンバージョン単価）", target_cpa_yen)
    t_roas = _opt_num("target_roas（目標の ROAS、%）", target_roas)
    today = today or _date.today()
    s, e = _date.fromisoformat(start), _date.fromisoformat(end)
    if e >= today:
        raise ValueError(f"終了日は昨日（{(today - _timedelta(days=1)).isoformat()}）まで。今日の分はまだ途中")
    if report.period_days(s, e) > 366:
        raise ValueError("期間は 366 日まで")
    if compare_start or compare_end:
        gaql.validate_period(compare_start, compare_end)
        cs, ce = _date.fromisoformat(compare_start), _date.fromisoformat(compare_end)
        label = "日付で指定"
    else:
        cs, ce, label = report.compare_period(s, e, str(compare).strip())
    if ce >= s and cs <= e:
        raise ValueError("比較期間が今の期間と重なっている")
    got = csvin.read(csv_path, media, columns)
    rows, how = got["行"], got["読み方"]
    cur = [r for r in rows if s.isoformat() <= r["date"] <= e.isoformat()]
    prev = [r for r in rows if cs.isoformat() <= r["date"] <= ce.isoformat()]
    if not cur:
        raise ValueError(f"今の期間（{s}〜{e}）の行が CSV に無い。CSV の日付は {how['日付の範囲']}")
    medias = sorted({r["media"] for r in cur + prev})
    head = {
        "対象アカウント": f"CSV: {how['ファイル']}（{'・'.join(medias)}）",
        "期間": f"今: {s}〜{e}（{report.period_days(s, e)} 日）／比較: {cs}〜{ce}（{report.period_days(cs, ce)} 日、{label}）。"
              "CSV の日付のまま（媒体の管理画面のタイムゾーン）",
        "単位": "金額は円、率は %、率の差は pt。内訳はキャンペーンごと",
    }
    bundle, nums = report.build(cur, prev, start=s, end=e, cstart=cs, cend=ce, compare_label=label, today=today,
                                lag_days=lag, target_cpa=t_cpa, target_roas=t_roas, max_rows=report.MAX_BREAKDOWN,
                                source=f"CSV（{how['ファイル']}。手元のファイルを読むだけ）", head=head)
    notes = list(bundle["注意"])
    if not prev:
        notes.insert(0, f"比較期間（{cs}〜{ce}）の行が CSV に無い。前の値は全部 0 で、差と増減率は比べられない。"
                        f"CSV の日付は {how['日付の範囲']}。比べるなら、比較期間も入るように書き出し直す。")
    have = {r["date"] for r in cur}
    gap = [d.isoformat() for d in report.date_list(s, e) if d.isoformat() not in have]
    if gap:
        notes.append(f"今の期間のうち {len(gap)} 日（{gap[0]} など）は CSV に行が無い。配信が無かった日か、書き出しの期間の外。0 として数えた。")
    notes.append("CSV の数字は書き出した時点のもの。成果は後から足されるので、同じ期間でも API や後の書き出しと合わないことがある。")
    notes.append("スプレッドシートにするときは report_sheet_plan(束ID, levels=False)。広告グループ別などの階層のタブは API の束だけ。")
    notes.extend(how["注意"])
    bundle["注意"] = notes
    bundle["CSV の読み方"] = how
    path = numcheck.save_bundle(bundle, nums, rows=sorted(prev + cur, key=lambda r: (r["date"], r["name"])))
    bundle["保存先"] = str(path)
    return bundle


def report_number_check(text: str, bundle_id: str, allow=None) -> dict:
    if not str(text or "").strip():
        raise ValueError("text（確かめる文章）が空")
    nums = numcheck.load_numbers(str(bundle_id).strip())
    out = numcheck.check(str(text), nums, allow)
    return {"対象アカウント": "なし（文章と束の突き合わせ）", "期間": f"束 {bundle_id} の期間", "単位": "数字 1 つごと", **out}


# ---------------- 段 4: 週次の運用レポート（自分用）の材料と骨組み ----------------

from . import templates  # noqa: E402

DECISION_LOOKBACK_DAYS = 28
EVAL_DAYS = 14   # 判断から評価までの目安（学習の期間は公式に約 2 週間。運用の型 04）


def _decision_results(cid: str, end: _date, today: _date, run: Search) -> List[dict]:
    rows = [r for r in decisions.read_all() if r.get("アカウント") == cid and r.get("日時")]
    lo = end - _timedelta(days=DECISION_LOOKBACK_DAYS - 1)
    out = []
    for r in sorted(rows, key=lambda x: x["日時"]):
        d = _date.fromisoformat(r["日時"][:10])
        if not lo <= d <= end:
            continue
        item = {"日": d.isoformat(), "判断": r.get("判断", ""), "誰の判断": r.get("誰の判断", ""), "対象": r.get("対象", ""),
                "記録の id": r.get("id", "")}
        ready = d + _timedelta(days=EVAL_DAYS)
        item["評価の時期"] = "来ている" if ready <= today else f"まだ早い（{ready.isoformat()} 以降）"
        if d < today - _timedelta(days=1):
            try:
                eff = measure_effect(cid, d.isoformat(), days_before=7, days_after=7, level="account", run=run, today=today)
                keep = ("表示回数", "クリック数", "費用（円）", "コンバージョン数", "コンバージョン単価（円）")
                item["前後の差（判断の日の前後 7 日、アカウント全体）"] = [
                    {k: v for k, v in x.items() if k in ("指標", "前（合計）", "後（合計）", "前", "後", "判定")}
                    for x in eff["合計"] if x["指標"] in keep]
                item["前後の期間"] = eff["期間"]
            except ValueError as ex:
                item["前後の差"] = f"出せない: {ex}"
        else:
            item["前後の差"] = "まだ後の日の実績が無い"
        out.append(item)
    return out


def _frozen_now(today: _date) -> Dict[str, tuple]:
    from .config import FROZEN_CAMPAIGNS
    return {k: v for k, v in FROZEN_CAMPAIGNS.items() if today <= _date.fromisoformat(v[0])}


def _hold_frozen(items: List[dict], frozen: Dict[str, tuple]) -> List[dict]:
    """固定中のキャンペーンを変える案は「様子を見る」に回す（見るだけの案はそのまま）。"""
    for x in items:
        if x["承認"].startswith("要る"):
            hit = next((k for k in frozen if k in x["出どころ"]), None)
            if hit:
                until, why = frozen[hit]
                x["段"] = "様子を見る"
                x["案"] = f"（{until} まで固定中・いまは変えない）{x['案']}"
                x["承認"] = "—（固定中）"
    return items


def _action_candidates(chk: Optional[dict], ano: Optional[dict], dec: List[dict],
                       frozen: Optional[Dict[str, tuple]] = None) -> List[dict]:
    items = []
    for f in (ano or {}).get("所見", []):
        items.append({"段": templates.ANOMALY_STAGE, "案": f"原因を確かめる（変えない）: {f['所見']}。{f.get('確かめること', '')}",
                      "重さ": f["重さ"], "出どころ": f"外れ {f['点検']}・{f['対象']}", "誰の案": monitor.OPINION,
                      "承認": "不要（見るだけ）"})
    for f in (chk or {}).get("所見", []):
        stage = templates.CHECK_STAGE.get(f["点検"], "素材")
        for fx in f.get("直し方の案", [])[:3]:
            st = "入札・予算" if fx["種類"] == checks.BID_KIND else stage
            items.append({"段": st, "案": fx["案"], "重さ": f["重さ"], "出どころ": f"点検 {f['点検']}・{f['対象']}",
                          "所見": f["所見"], "誰の案": checks.OPINION, "承認": "要る（アカウントを変える）"})
    for d in dec:
        if d["評価の時期"].startswith("まだ早い"):
            items.append({"段": "様子を見る", "案": f"{d['日']} の判断「{d['判断'][:40]}」は評価を待つ。{d['評価の時期']}",
                          "重さ": "小", "出どころ": f"判断の記録 {d['記録の id']}", "誰の案": "道具（時間の法則）",
                          "承認": "不要"})
    return templates.order_actions(_hold_frozen(items, frozen or {}))


def _md_escape(s) -> str:
    return str(s).replace("|", "／").replace("\n", " ")


def _skeleton(t: dict, b: dict, ano, chg, chk, dec, cand, missing, s: _date, e: _date,
              frozen: Optional[Dict[str, tuple]] = None) -> str:
    L = [f"# {t['名前']} {s}〜{e}", "", f"- 対象: {b['対象アカウント']}", f"- 比べる相手: {b['期間']}",
         f"- 束ID: {b['束ID']}（数字はこの束から。書き終えたら report_number_check）"]
    if b["成果の遅れ"]["未確定の日数"]:
        L.append(f"- 暫定: {b['成果の遅れ']['注意']}")
    for k, (until, why) in (frozen or {}).items():
        L.append(f"- 固定中: {k}（{until} まで。{why}）")
    L += ["", "## 1. いちばん大事なこと", "【書く: 三行まで。事実だけ。材料は「外れと点検の所見」「目標との差」「合計の表」】", ""]
    L += ["## 2. 前回決めたこと → その結果"]
    if dec:
        for d in dec:
            L.append(f"- {d['日']} {_md_escape(d['判断'][:80])}（{d['誰の判断']}）。評価の時期: {d['評価の時期']}")
            for x in d.get("前後の差（判断の日の前後 7 日、アカウント全体）", []):
                before = x.get("前（合計）", x.get("前"))
                after = x.get("後（合計）", x.get("後"))
                L.append(f"  - {x['指標']}: {before} → {after}（{x.get('判定', '')}）")
            if isinstance(d.get("前後の差"), str):
                L.append(f"  - 前後の差: {d['前後の差']}")
        L.append("【書く: 一件ごとに、結果の読み（事実と推測を分ける）】")
    else:
        L.append("- 判断の記録なし（今週決めたことから log_decision で残す）")
    L += ["", "## 3. 数字（前週・目標・計画と比べて）", b["表"]["合計"], ""]
    for r in b.get("目標・計画との差", []):
        if "判定" in r:
            L.append(f"- {r['項目']}: 目標 {r['目標']:,}{'%' if r['単位'] == '%' else ' 円'} に対して 実績 "
                     f"{'—' if r['実績'] is None else format(r['実績'], ',')}{'%' if r['単位'] == '%' else ' 円'}（{r['判定']}）")
        else:
            u = " 円" if r["単位"] == "円" else " 件"
            L.append(f"- {r['項目']}: {r['計画の期間']}、経過分の実績 {r['経過分の実績']:,}{u}、按分した計画 "
                     f"{r['経過日数で按分した計画']:,}{u}、進み具合 {r['進み具合（%）']}%（{str(r.get('帯', '')).split('（')[0]}）")
    L += ["", b["表"]["要因の分解"], "", "【書く: いちばん大きい要因を一〜二行（寄与の割合を使う）】", ""]
    if b["表"].get("日別"):
        L += ["日別:", "", b["表"]["日別"], ""]
    L += ["## 4. 内訳", b["表"].get("内訳", "（実績のある対象なし）"), "", "【書く: 寄与の大きい対象と、新しく出た・止まった対象】", ""]
    L += ["## 5. 期間中の変更"]
    if chg is None:
        L.append("- 読めなかった（「確かめていないこと」を見る）")
    elif not chg["件数"]["変更"]:
        L.append("- 変更なし（API の変更の履歴。Google 広告エディタの変更は出ない）")
    else:
        routes = "、".join(f"{k} {v} 件" for k, v in chg["件数"]["経路別"].items())
        L.append(f"- 変更 {chg['件数']['変更']} 件（{routes}）")
        for r in chg["行"][:8]:
            L.append(f"  - {str(r.get('日時', ''))[:16]} {r.get('種類', '')} {r.get('操作', '')}（{r.get('経路', '')}）{_md_escape(r.get('キャンペーン') or '')}")
        if chg["件数"]["人が画面で変えたとは限らない経路"]:
            L.append(f"- 人が画面で変えたとは限らない経路: {chg['件数']['人が画面で変えたとは限らない経路']} 件")
    L += ["", "## 6. 外れと点検の所見"]
    fs = [("外れ", f) for f in (ano or {}).get("所見", [])] + [("点検", f) for f in (chk or {}).get("所見", [])]
    if not fs:
        L.append("- 所見なし")
    for kind, f in fs:
        L.append(f"- [{f['重さ']}] {kind} {f['点検']}・{_md_escape(f['対象'])}: {_md_escape(f['所見'])}")
    n_unknown = len((ano or {}).get("判定できなかったもの", [])) + len((chk or {}).get("判定できなかったもの", []))
    if n_unknown:
        L.append(f"- 判定できなかったもの: {n_unknown} 件（件数不足など。問題なしという意味ではない）")
    L += ["", "## 7. 推測（仮説）", "【書く: 二〜四個。それぞれ「推測」の印と「何を見れば確かめられるか」】", ""]
    L += ["## 8. ネクストアクション", "候補（打ち手の段の順。入札・予算は最後）:", "", "| 順 | 段 | 案 | 出どころ | 承認 |", "|--:|---|---|---|---|"]
    for c in cand[:12]:
        L.append(f"| {c['順']} | {c['段']} | {_md_escape(c['案'])[:120]} | {_md_escape(c['出どころ'])} | {c['承認']} |")
    if not cand:
        L.append("| — | 様子を見る | 候補なし（所見も評価待ちの判断も無い） | — | — |")
    L += ["", "【書く: 三〜五個に絞る。一つごとに 何を／なぜ（根拠の数字）／段／誰の案／承認が要るか／いつ・何で評価するか。決めたら log_decision】", ""]
    L += ["## 9. 確かめていないこと・暫定"]
    L.append(f"- 成果の遅れ: {b['成果の遅れ']['注意']}")
    for m in missing:
        L.append(f"- 読めなかった材料: {m}")
    return "\n".join(L) + "\n"


def report_weekly_self(customer_id: str, end: str = "", target_cpa_yen=None, target_roas=None,
                       plan_cost_yen=None, plan_conversions=None, plan_start: str = "", plan_end: str = "",
                       include_checks: bool = True, lag_days: int = report.LAG_DAYS,
                       run: Optional[Search] = None, today: Optional[_date] = None) -> dict:
    run = run or _search
    cid = gaql.normalize_customer_id(customer_id)
    if end:
        gaql.validate_period(end, end)
    acct = _account(cid, run)
    convert.require_jpy(acct["通貨"])
    today = today or _today(acct)
    e = _date.fromisoformat(end) if end else today - _timedelta(days=1)
    s = e - _timedelta(days=6)
    t = templates.WEEKLY_SELF
    b = report_numbers(cid, s.isoformat(), e.isoformat(), "previous", target_cpa_yen=target_cpa_yen,
                       target_roas=target_roas, plan_cost_yen=plan_cost_yen, plan_conversions=plan_conversions,
                       plan_start=plan_start, plan_end=plan_end, lag_days=lag_days, run=run, today=today)
    missing, extra = [], []
    try:
        ano = get_anomalies(cid, e.isoformat(), run=run, today=today)
        extra += numcheck.flatten({"まとめ": ano["まとめ"], "所見": [f.get("根拠の数字") for f in ano["所見"]],
                                   "見た日の数字": ano["見た日の数字"]}, "外れ")
    except ValueError as ex:
        ano = None
        missing.append(f"外れ（get_anomalies）: {ex}")
    try:
        chg = get_change_history(cid, s.isoformat(), e.isoformat(), top_n=20, run=run, today=today)
        extra += numcheck.flatten({"件数": chg["件数"]}, "変更")
    except ValueError as ex:
        chg = None
        missing.append(f"変更の履歴（get_change_history）: {ex}")
    chk = None
    if include_checks:
        try:
            chk = run_checks(cid, s.isoformat(), e.isoformat(), include_lp=False, run=run)
            extra += numcheck.flatten({"まとめ": chk["まとめ"], "所見": [f.get("根拠") for f in chk["所見"]]}, "点検")
        except ValueError as ex:
            missing.append(f"点検（run_checks）: {ex}")
    n_unknown = len((ano or {}).get("判定できなかったもの", [])) + len((chk or {}).get("判定できなかったもの", []))
    extra.append({"項目": "判定できなかったものの数（外れと点検）", "値": n_unknown, "単位": "件"})
    extra.append({"項目": "外れの所見の数", "値": len((ano or {}).get("所見", [])), "単位": "件"})
    extra.append({"項目": "点検の所見の数", "値": len((chk or {}).get("所見", [])), "単位": "件"})
    # 評価の目安（運用の型 04・知識ベース 36 の 5-6。公式の数字ではない目安を含む）
    extra.append({"項目": "評価の目安: 成果の件数（36 の 5-6 の目安）", "値": monitor.MIN_EVENTS, "単位": "件"})
    extra.append({"項目": "評価の目安: 判断から評価までの日数（学習の期間 約 2 週間）", "値": EVAL_DAYS, "単位": "日"})
    extra.append({"項目": "1 日の費用の上限（日予算の倍率。14 の 98【公式】）", "値": monitor.DAILY_CAP, "単位": "倍"})
    dec = _decision_results(cid, e, today, run)
    extra += numcheck.flatten(dec, "判断")
    frozen = _frozen_now(today)
    cand = _action_candidates(chk, ano, dec, frozen)
    added = numcheck.add_numbers(b["束ID"], extra)
    md = _skeleton(t, b, ano, chg, chk, dec, cand, missing, s, e, frozen)
    path = out_dir() / f"weekly_self_{cid}_{e.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    return {
        "対象アカウント": b["対象アカウント"],
        "期間": f"今: {s}〜{e}（7 日）／前: {(s - _timedelta(days=7))}〜{(s - _timedelta(days=1))}（7 日）（{acct['タイムゾーン']} の日付）",
        "単位": "金額は円、率は %、率の差は pt",
        "型": {k: t[k] for k in ("名前", "読み手", "目的", "長さ", "言葉", "節", "ネクストアクションの決まり", "共通の決まり")},
        "束ID": b["束ID"],
        "骨組み": md,
        "骨組みの保存先": str(path),
        "次にやることの候補": cand,
        "材料の数": {"外れの所見": len((ano or {}).get("所見", [])), "点検の所見": len((chk or {}).get("所見", [])),
                  "期間中の変更": (chg or {}).get("件数", {}).get("変更"), "期間の判断の記録": len(dec),
                  "束に足した数字": added},
        "読めなかった材料": missing,
        "書き方": ["骨組みの【書く: …】を埋め、ほかの行と表はそのまま使う。【書く】の指示の行は消す。",
                 "書き終えたら report_number_check(text, 束ID) を通す。「直すもの」が 0 件になってから出す。",
                 "ネクストアクションは候補から選ぶか、根拠の数字を添えて足す。決めたものは log_decision で残す（誰の判断かを正しく）。"],
        "ほかの型": templates.NOT_YET,
    }


# ---------------- 段 4: レポートを手元の HTML のページにする ----------------

from pathlib import Path  # noqa: E402

from . import report_html  # noqa: E402


def report_to_html(md_path: str = "", text: str = "", bundle_id: str = "", allow=None) -> dict:
    """Markdown のレポートを HTML にする。先に数字を束と突き合わせ、合わない数字があれば結果に出す（ページは作る）。"""
    src = None
    if md_path:
        src = Path(md_path).expanduser()
        if src.suffix.lower() != ".md" or not src.is_file():
            raise ValueError(f"md_path は手元の .md のファイルを指す（受け取った値: {md_path!r}）")
        text = src.read_text(encoding="utf-8")
    if not str(text or "").strip():
        raise ValueError("md_path か text（レポートの Markdown）を渡す")
    bid = (bundle_id or report_html.find_bundle_id(text) or "").strip()
    if not bid:
        raise ValueError("束ID が文章に無い。bundle_id を渡す（report_numbers か report_weekly_self の返した「束ID」）")
    bundle = report_html.load_bundle(bid)
    chk = report_number_check(text, bid, allow)
    page = report_html.render(text, bundle)
    dest = src.with_suffix(".html") if src else out_dir() / f"report_{bid}.html"
    dest.write_text(page, encoding="utf-8")
    return {
        "対象アカウント": bundle.get("対象アカウント", "—"),
        "期間": bundle.get("期間", "—"),
        "単位": "ページ 1 枚（手元のファイル。公開しない）",
        "保存先": str(dest),
        "数字の突き合わせ": {"結果": chk["結果"], "件数": chk["件数"], "直すもの": chk["直すもの"]},
        "注記": ["図は束から描き、文と表は Markdown をそのまま変換した。数字を直すときは Markdown を直して作り直す。",
               "フォントは Google Fonts から読む（開くときにネットにつながっていれば）。ほかに外へは何も送らない。"],
    }


# ---------------- 段 4: レポートを Google スプレッドシートに（書く中身を作るだけ） ----------------

import re  # noqa: E402

from . import sheet as _sheet  # noqa: E402


DETAIL_ORDER = ("ad_group", "ad", "keyword", "search_term")


def _detail_rows(cid: str, cond: dict, run: Search) -> List[dict]:
    """階層ごとの日別の行（今の期間と比べる期間）。読むだけ。"""
    out = []
    for period in (cond["今の期間"], cond["比較期間"]):
        lo, hi = period.split("〜")
        for kind in DETAIL_ORDER:
            out += fetch.detail_daily(cid, kind, lo, hi, run)
    return out


def _id_list(label: str, v, as_int: bool) -> list:
    if v in (None, "", [], ()):
        return []
    items = v if isinstance(v, (list, tuple)) else re.split(r"[,\s]+", str(v).strip())
    out = []
    for x in items:
        t = str(x).strip()
        if not t:
            continue
        if not t.isdigit():
            raise ValueError(f"{label}は数字の id を並べて渡す（受け取った値: {x!r}）")
        out.append(int(t) if as_int else t)
    return out


def report_sheet_plan(bundle_id: str, md_path: str = "", tab: str = "", levels: bool = True, weeks: int = 8,
                      refresh: bool = False, replace_sheet_ids="", replace_named_range_ids="",
                      run: Optional[Search] = None) -> dict:
    """tab は版 0.16.0 から使わない（タブの名前は決まった並び）。呼び方を変えないために残している。"""
    data = numcheck.load_file(str(bundle_id).strip())
    rows = data.get("元の行")
    if not rows:
        raise ValueError(f"束 {bundle_id} に元の行が無い（版 0.12.0 より前の束）。report_numbers か report_weekly_self を呼び直す")
    weeks = _int_in("weeks（週別の推移の週の数）", weeks, 0, 26)
    replace_sheets = _id_list("replace_sheet_ids（消すタブの sheetId）", replace_sheet_ids, True)
    replace_named = _id_list("replace_named_range_ids（消す名前付き範囲の id）", replace_named_range_ids, False)
    md = ""
    if md_path:
        p = Path(md_path).expanduser()
        if p.suffix.lower() != ".md" or not p.is_file():
            raise ValueError(f"md_path は手元の .md のファイルを指す（受け取った値: {md_path!r}）")
        md = p.read_text(encoding="utf-8")
    b = data["束"]
    cid = re.match(r"\d+", str(b.get("対象アカウント", "")))
    e = _date.fromisoformat(b["条件"]["今の期間"].split("〜")[1])
    dec = []
    if cid:
        dec = [x for x in decisions.read_all() if x.get("アカウント") == cid.group(0)]
        dec = [{"日": x["日時"][:10], "判断": x.get("判断", ""), "誰の判断": x.get("誰の判断", ""), "対象": x.get("対象", ""),
                "評価の時期": "", "記録の id": x.get("id", "")} for x in dec if x.get("日時", "")[:10] <= e.isoformat()]
    run_at = _datetime.fromtimestamp(numcheck.bundle_path(bundle_id).stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    details, trend, changed = None, None, False
    if levels:
        if not cid:
            raise ValueError("束にアカウントの ID が無いので、階層ごとの行を読めない（levels=False なら階層のタブを作らない）")
        acct_id = gaql.normalize_customer_id(cid.group(0))
        # 一度読んだ行は束のファイルに残っているので、それを使う（シートの数字を、前に照らした数字とそろえる）。refresh=True で読み直す
        details = None if refresh else data.get("詳細の行")
        if details is None:
            details = _detail_rows(acct_id, b["条件"], run or _search)
            data["詳細の行"] = details
            changed = True
        if weeks:
            span = f"{(e - _timedelta(days=7 * weeks - 1)).isoformat()}〜{e.isoformat()}"
            trend = None if refresh or data.get("推移の期間") != span else data.get("推移の行")
            if trend is None:
                lo, hi = span.split("〜")
                trend = _api_rows(fetch.daily(acct_id, "campaign", lo, hi, run or _search), "campaign")
                data["推移の行"], data["推移の期間"] = trend, span
                changed = True
    if changed:
        numcheck.bundle_path(bundle_id).write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out = _sheet.plan(b, rows, md=md, decisions=dec, run_at=run_at, details=details, trend=trend, weeks=weeks or 8,
                      replace_sheets=replace_sheets, replace_named=replace_named)
    return {"対象アカウント": b.get("対象アカウント", "—"), "期間": b.get("期間", "—"),
            "単位": "Google スプレッドシートに書く値・式・書式（ads-mcp はシートに書かない）", "束ID": bundle_id, **out}


def report_sheet_check(bundle_id: str, values) -> dict:
    data = numcheck.load_file(str(bundle_id).strip())
    if not isinstance(values, list) or not all(isinstance(r, list) for r in values):
        raise ValueError("values は get_values の values（行の配列）をそのまま渡す")
    b = data["束"]
    base = data.get("元の行") or []
    details = (data.get("詳細の行") or []) + _sheet.campaign_details(base, b["条件"])
    all_rows = _sheet.merge_rows(base, data.get("推移の行"), b["条件"])
    return {"対象アカウント": b.get("対象アカウント", "—"), "期間": b.get("期間", "—"),
            "単位": "表の 1 ます ごと", **_sheet.check(b, values, details, all_rows)}
