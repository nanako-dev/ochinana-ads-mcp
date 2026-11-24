"""API の行を、ただの辞書に直す。点検や整形はこの辞書だけを見る（API を知らなくてよい）。

どれも読み取りだけ。値は API の英語の名前のまま持ち、日本語に直すのは返答を作る側でする。
"""
from __future__ import annotations

from typing import Callable, Dict, List

from . import gaql

Search = Callable[[str, str], List]


def _metrics(m) -> dict:
    return {
        "impressions": int(m.impressions),
        "clicks": int(m.clicks),
        "cost_micros": int(m.cost_micros),
        "conversions": float(m.conversions),
        "conversions_value": float(m.conversions_value),
    }


def campaigns(cid: str, start: str, end: str, run: Search) -> List[dict]:
    """削除済み以外のキャンペーンの設定に、期間の実績と表示の割合を合わせる。"""
    out: Dict[int, dict] = {}
    for r in run(cid, gaql.campaign_settings_query()):
        c = r.campaign
        out[int(c.id)] = {
            "id": int(c.id), "name": c.name, "status": c.status.name,
            "channel": c.advertising_channel_type.name, "bidding": c.bidding_strategy_type.name,
            "geo_type": c.geo_target_type_setting.positive_geo_target_type.name,
            "budget_micros": int(r.campaign_budget.amount_micros),
            "impressions": 0, "clicks": 0, "cost_micros": 0, "conversions": 0.0,
            "search_is": None, "rank_lost": None, "budget_lost": None,
        }
    for r in run(cid, gaql.campaign_share_query(start, end)):
        c = out.get(int(r.campaign.id))
        if c is None:
            continue
        m = r.metrics
        c.update(impressions=int(m.impressions), clicks=int(m.clicks), cost_micros=int(m.cost_micros),
                 conversions=float(m.conversions))
        if int(m.impressions) > 0:
            c.update(search_is=float(m.search_impression_share),
                     rank_lost=float(m.search_rank_lost_impression_share),
                     budget_lost=float(m.search_budget_lost_impression_share))
    return list(out.values())


def conversion_setup(cid: str, run: Search) -> dict:
    q = gaql.conversion_goals_queries()
    actions = {}
    for r in run(cid, q["actions"]):
        a = r.conversion_action
        actions[a.resource_name] = {"name": a.name, "category": a.category.name, "origin": a.origin.name,
                                    "status": a.status.name, "primary": bool(a.primary_for_goal)}
    custom = {}
    for r in run(cid, q["custom_goals"]):
        g = r.custom_conversion_goal
        custom[g.resource_name] = {"name": g.name, "status": g.status.name, "actions": list(g.conversion_actions)}
    config = {}
    for r in run(cid, q["goal_config"]):
        g = r.conversion_goal_campaign_config
        config[int(r.campaign.id)] = {"level": g.goal_config_level.name, "custom_goal": g.custom_conversion_goal or None}
    goals = []
    for r in run(cid, q["goals"]):
        g = r.campaign_conversion_goal
        goals.append({"campaign_id": int(r.campaign.id), "category": g.category.name, "origin": g.origin.name,
                      "biddable": bool(g.biddable)})
    return {"actions": actions, "custom_goals": custom, "config": config, "goals": goals}


def ad_groups(cid: str, run: Search) -> List[dict]:
    return [
        {"campaign_id": int(r.campaign.id), "id": int(r.ad_group.id), "name": r.ad_group.name,
         "status": r.ad_group.status.name}
        for r in run(cid, gaql.ad_groups_query())
    ]


def ads(cid: str, run: Search) -> List[dict]:
    out = []
    for r in run(cid, gaql.ads_query()):
        a = r.ad_group_ad
        rsa = a.ad.responsive_search_ad
        out.append({
            "campaign_id": int(r.campaign.id), "ad_group_id": int(r.ad_group.id),
            "ad_id": int(a.ad.id), "type": a.ad.type_.name, "status": a.status.name,
            "strength": a.ad_strength.name,
            "approval": a.policy_summary.approval_status.name, "review": a.policy_summary.review_status.name,
            "headlines": [h.text for h in rsa.headlines], "descriptions": [d.text for d in rsa.descriptions],
            "final_urls": list(a.ad.final_urls), "path1": rsa.path1, "path2": rsa.path2,
        })
    return out


def asset_links(cid: str, run: Search) -> List[dict]:
    q = gaql.asset_links_queries()
    out = []
    for r in run(cid, q["account"]):
        x = r.customer_asset
        out.append({"level": "account", "campaign_id": None, "ad_group_id": None, "field_type": x.field_type.name,
                    "status": x.status.name, "primary_status": x.primary_status.name,
                    "approval": r.asset.policy_summary.approval_status.name})
    for r in run(cid, q["campaign"]):
        x = r.campaign_asset
        out.append({"level": "campaign", "campaign_id": int(r.campaign.id), "ad_group_id": None,
                    "field_type": x.field_type.name, "status": x.status.name,
                    "primary_status": x.primary_status.name,
                    "approval": r.asset.policy_summary.approval_status.name})
    for r in run(cid, q["ad_group"]):
        x = r.ad_group_asset
        out.append({"level": "ad_group", "campaign_id": int(r.campaign.id), "ad_group_id": int(r.ad_group.id),
                    "field_type": x.field_type.name, "status": x.status.name,
                    "primary_status": x.primary_status.name,
                    "approval": r.asset.policy_summary.approval_status.name})
    return out


def keywords(cid: str, run: Search) -> List[dict]:
    """入札するキーワードと配信の状態。"""
    out = []
    for r in run(cid, gaql.keyword_serving_query()):
        c = r.ad_group_criterion
        out.append({"campaign_id": int(r.campaign.id), "ad_group_id": int(r.ad_group.id),
                    "text": c.keyword.text, "match": c.keyword.match_type.name, "status": c.status.name,
                    "serving": c.system_serving_status.name})
    return out


def negatives(cid: str, run: Search) -> List[dict]:
    """除外キーワード。効く範囲（キャンペーン、広告グループ）つき。共有リストは付け先のキャンペーンに広げる。"""
    q = gaql.keywords_queries()
    out = []
    for r in run(cid, q["ad_group"]):
        c = r.ad_group_criterion
        if c.negative:
            out.append({"campaign_id": int(r.campaign.id), "ad_group_id": int(r.ad_group.id),
                        "text": c.keyword.text, "match": c.keyword.match_type.name})
    for r in run(cid, q["campaign"]):
        c = r.campaign_criterion
        if c.negative:
            out.append({"campaign_id": int(r.campaign.id), "ad_group_id": None,
                        "text": c.keyword.text, "match": c.keyword.match_type.name})
    used: Dict[int, List[int]] = {}
    for r in run(cid, q["shared_list_usage"]):
        used.setdefault(int(r.shared_set.id), []).append(int(r.campaign.id))
    for r in run(cid, q["shared_list"]):
        c = r.shared_criterion
        account_wide = r.shared_set.type_.name == "ACCOUNT_LEVEL_NEGATIVE_KEYWORDS"
        targets = [None] if account_wide else used.get(int(r.shared_set.id), [])
        for campaign_id in targets:
            out.append({"campaign_id": campaign_id, "ad_group_id": None,
                        "text": c.keyword.text, "match": c.keyword.match_type.name,
                        "account_wide": account_wide})
    return out


def search_terms(cid: str, start: str, end: str, run: Search) -> List[dict]:
    out = []
    for r in run(cid, gaql.search_terms_query(start, end)):
        out.append({"term": r.search_term_view.search_term, "campaign_id": int(r.campaign.id),
                    "ad_group_id": int(r.ad_group.id), "status": r.search_term_view.status.name,
                    **_metrics(r.metrics)})
    return out


def generated(cid: str, start: str, end: str, run: Search) -> dict:
    q = gaql.generated_text_queries(start, end)
    texts, combos, pages = [], [], []
    for r in run(cid, q["texts"]):
        v = r.ad_group_ad_asset_view
        texts.append({"campaign": r.campaign.name, "ad_group": r.ad_group.name, "field_type": v.field_type.name,
                      "label": v.performance_label.name, "text": r.asset.text_asset.text,
                      "link_source": v.source.name, "asset_source": r.asset.source.name, **_metrics(r.metrics)})
    for r in run(cid, q["combinations"]):
        v = r.ai_max_search_term_ad_combination_view
        combos.append({"campaign": r.campaign.name, "ad_group": r.ad_group.name, "search_term": v.search_term,
                       "headline": v.headline, "landing_page": v.landing_page, **_metrics(r.metrics)})
    for r in run(cid, q["landing_pages"]):
        pages.append({"campaign": r.campaign.name, "ad_group": r.ad_group.name,
                      "url": r.landing_page_view.unexpanded_final_url,
                      "source": r.segments.landing_page_source.name, **_metrics(r.metrics)})
    return {"texts": texts, "combinations": combos, "landing_pages": pages}


# ---- 段 3 ----

def keyword_history_rows(cid: str, start: str, end: str, run: Search) -> List[dict]:
    """キーワードごとの実績と表示される割合（検索キャンペーンだけ）。"""
    out = []
    for r in run(cid, gaql.keyword_share_query(start, end)):
        if r.campaign.advertising_channel_type.name != "SEARCH":
            continue
        m = r.metrics
        out.append({"text": r.ad_group_criterion.keyword.text, "impressions": int(m.impressions),
                    "clicks": int(m.clicks), "cost_micros": int(m.cost_micros),
                    "search_is": float(m.search_impression_share) or None})
    return out


def account_state(cid: str, run: Search) -> dict:
    """知見カードと照らすための、アカウントのいまの状態（道具で読める範囲）。"""
    campaigns = []
    for r in run(cid, gaql.campaign_state_query()):
        c = r.campaign
        campaigns.append({"id": int(c.id), "name": c.name, "status": c.status.name,
                          "channel": c.advertising_channel_type.name, "bidding": c.bidding_strategy_type.name,
                          "ai_max": bool(c.ai_max_setting.enable_ai_max)})
    languages = sum(1 for r in run(cid, gaql.targeting_query()) if r.campaign_criterion.type_.name == "LANGUAGE")
    return {"campaigns": campaigns, "language_criteria": languages}


# ---- 段 3.6 ----
import re as _re  # noqa: E402

VALUE_CHARS = 200
MAX_LIST_VALUES = 10


def _snake(path: str) -> str:
    return _re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", path).lower()


def _get(obj, name: str):
    for n in (name, name + "_"):  # type → type_ のように、Python の予約語は末尾に _ が付く
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def plain(v):
    """API の値を、返答に入れられるただの値にする（列挙は英語の名前、入れ子は短い文字列）。"""
    if v is None or isinstance(v, (bool, int, float)) and not hasattr(v, "name"):
        return v
    if isinstance(v, str):
        return v if len(v) <= VALUE_CHARS else v[:VALUE_CHARS] + "…"
    if hasattr(v, "name") and isinstance(v, int):  # proto の列挙
        return v.name
    if isinstance(v, (list, tuple)) or (hasattr(v, "__iter__") and hasattr(v, "__len__") and not hasattr(v, "keys")
                                        and not hasattr(v, "_pb")):
        items = [plain(x) for x in list(v)[:MAX_LIST_VALUES]]
        return items + (["…"] if len(v) > MAX_LIST_VALUES else [])
    text = _re.sub(r"\s+", " ", str(v)).strip()
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS] + "…"


def field_value(resource, path: str):
    """変わった項目の値を、点で区切った道（例: target_cpa.target_cpa_micros）でたどって取る。"""
    obj = resource
    for part in _snake(path).split("."):
        if obj is None:
            return None
        obj = _get(obj, part)
    return obj


def _id_from(resource_name: str) -> int | None:
    m = _re.search(r"/(\d+)$", str(resource_name or ""))
    return int(m.group(1)) if m else None


def change_events(cid: str, start: str, end_next: str, limit: int, run: Search) -> List[dict]:
    """変更の履歴を、ただの辞書の並びにする。だれが変えたか（メールアドレス）は読まない。"""
    out = []
    for r in run(cid, gaql.change_events_query(start, end_next, limit)):
        e = r.change_event
        rtype = e.change_resource_type.name
        op = e.resource_change_operation.name
        key = rtype.lower()
        old_res, new_res = _get(e.old_resource, key), _get(e.new_resource, key)
        fields = []
        for p in list(getattr(e.changed_fields, "paths", []) or []):
            old = None if op == "CREATE" else plain(field_value(old_res, p))
            new = None if op == "REMOVE" else plain(field_value(new_res, p))
            fields.append({"path": p, "old": old, "new": new})
        out.append({
            "time": str(e.change_date_time), "resource_type": rtype, "operation": op,
            "client_type": e.client_type.name, "resource_name": e.change_resource_name,
            "campaign_id": _id_from(e.campaign), "ad_group_id": _id_from(e.ad_group), "fields": fields,
        })
    return out


def campaign_names(cid: str, run: Search) -> Dict[int, str]:
    return {int(r.campaign.id): r.campaign.name for r in run(cid, gaql.campaign_names_query())}


def daily(cid: str, level: str, start: str, end: str, run: Search, campaign_id: str = "") -> List[dict]:
    """日別の実績。キャンペーン単位は、いまの 1 日の予算（micros）と状態も持つ。"""
    out = []
    for r in run(cid, gaql.daily_query(level, start, end, campaign_id)):
        d = {"date": str(r.segments.date), "campaign_id": int(r.campaign.id), "campaign": r.campaign.name,
             **_metrics(r.metrics)}
        if level == "ad_group":
            d.update(ad_group_id=int(r.ad_group.id), ad_group=r.ad_group.name, status=r.ad_group.status.name)
        else:
            d.update(status=r.campaign.status.name, channel=r.campaign.advertising_channel_type.name,
                     budget_micros=int(r.campaign_budget.amount_micros))
        out.append(d)
    return out


AD_TYPE_JA = {"RESPONSIVE_SEARCH_AD": "レスポンシブ検索広告", "EXPANDED_TEXT_AD": "拡張テキスト広告",
              "RESPONSIVE_DISPLAY_AD": "レスポンシブ ディスプレイ広告", "VIDEO_AD": "動画広告"}


def detail_daily(cid: str, kind: str, start: str, end: str, run: Search) -> List[dict]:
    """階層ごとの日別の行を、レポートの共通の形（report.py の冒頭）に。費用は円。"""
    from . import convert
    out = []
    level = gaql.DETAIL_KINDS[kind]
    for r in run(cid, gaql.detail_daily_query(kind, start, end)):
        d = {"date": str(r.segments.date), "media": "Google 広告", "level": level, "campaign": r.campaign.name,
             "ad_group": r.ad_group.name, "sub": ""}
        if kind == "ad_group":
            d.update(key=str(r.ad_group.id), name=r.ad_group.name)
        elif kind == "ad":
            a = r.ad_group_ad.ad
            heads = [h.text for h in getattr(a.responsive_search_ad, "headlines", [])][:1]
            d.update(key=str(a.id), name=f"広告 {a.id}" + (f"（{heads[0]}）" if heads else ""),
                     sub=convert.ja(AD_TYPE_JA, a.type_.name if hasattr(a, "type_") else a.type.name))
        elif kind == "keyword":
            c = r.ad_group_criterion
            d.update(key=f"{r.ad_group.id}:{c.criterion_id}", name=c.keyword.text,
                     sub=convert.ja(convert.MATCH_JA, c.keyword.match_type.name))
        else:
            t = r.search_term_view.search_term
            d.update(key=f"{r.ad_group.id}:{t}", name=t)
        m = _metrics(r.metrics)
        d.update(impressions=m["impressions"], clicks=m["clicks"], cost=m["cost_micros"] / 1_000_000,
                 conversions=m["conversions"], value=m["conversions_value"])
        out.append(d)
    return out
