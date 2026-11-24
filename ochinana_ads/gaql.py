"""GAQL（Google 広告の問い合わせ文）を組み立てる。API は呼ばない。

文に差し込む値は、ここで形を確かめたアカウント ID と日付だけ。自由な文字列は差し込まない。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, Tuple

from .config import ALLOWED_CUSTOMER_IDS

METRICS = (
    "metrics.impressions, metrics.clicks, metrics.cost_micros, "
    "metrics.conversions, metrics.conversions_value"
)

LEVELS = {
    "campaign": "campaign",
    "キャンペーン": "campaign",
    "ad_group": "ad_group",
    "adgroup": "ad_group",
    "広告グループ": "ad_group",
    "keyword": "keyword",
    "キーワード": "keyword",
}
LEVEL_JA = {"campaign": "キャンペーン", "ad_group": "広告グループ", "keyword": "キーワード"}


def normalize_customer_id(customer_id: str) -> str:
    """ハイフンや空白を取り、許可したアカウントかを確かめる。"""
    cid = re.sub(r"[\s\-]", "", str(customer_id))
    if not re.fullmatch(r"\d{10}", cid):
        raise ValueError(f"アカウント ID は 10 桁の数字で指定する（受け取った値: {customer_id!r}）")
    if cid not in ALLOWED_CUSTOMER_IDS:
        raise ValueError(
            f"アカウント {cid} は許可リストに無いので読まない。"
            f"使えるのは {', '.join(ALLOWED_CUSTOMER_IDS)}"
        )
    return cid


def validate_period(start: str, end: str) -> Tuple[str, str]:
    """日付は YYYY-MM-DD。開始が終了より後なら断る。"""
    out = []
    for label, v in (("開始日", start), ("終了日", end)):
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError(f"{label}は YYYY-MM-DD で指定する（受け取った値: {v!r}）")
        try:
            out.append(date.fromisoformat(v))
        except ValueError:
            raise ValueError(f"{label}が実在しない日付: {v}") from None
    if out[0] > out[1]:
        raise ValueError(f"開始日 {start} が終了日 {end} より後になっている")
    return start, end


def normalize_level(level: str) -> str:
    key = str(level).strip().lower()
    if key not in LEVELS:
        raise ValueError("単位は campaign / ad_group / keyword のどれか（日本語でも可）")
    return LEVELS[key]


def _between(start: str, end: str) -> str:
    start, end = validate_period(start, end)
    return f"segments.date BETWEEN '{start}' AND '{end}'"


def account_query() -> str:
    return (
        "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
        "customer.time_zone, customer.manager FROM customer LIMIT 1"
    )


def targeting_query() -> str:
    """削除済み以外のキャンペーンの、配信地域と言語の指定（除外を除く）。"""
    return (
        "SELECT campaign.id, campaign_criterion.type, "
        "campaign_criterion.location.geo_target_constant, "
        "campaign_criterion.language.language_constant "
        "FROM campaign_criterion "
        "WHERE campaign_criterion.type IN ('LOCATION', 'LANGUAGE') "
        "AND campaign_criterion.negative = FALSE AND campaign.status != 'REMOVED'"
    )


def geo_name_query(resource_name: str) -> str:
    if not re.fullmatch(r"geoTargetConstants/\d+", resource_name):
        raise ValueError(f"地域の指定の形が違う: {resource_name!r}")
    return (
        "SELECT geo_target_constant.id, geo_target_constant.name, geo_target_constant.country_code "
        f"FROM geo_target_constant WHERE geo_target_constant.resource_name = '{resource_name}'"
    )


def language_name_query(resource_name: str) -> str:
    if not re.fullmatch(r"languageConstants/\d+", resource_name):
        raise ValueError(f"言語の指定の形が違う: {resource_name!r}")
    return (
        "SELECT language_constant.id, language_constant.name, language_constant.code "
        f"FROM language_constant WHERE language_constant.resource_name = '{resource_name}'"
    )


def performance_query(level: str, start: str, end: str) -> str:
    """期間の合計を、指定した単位で 1 行ずつ。日別には割らない。"""
    level = normalize_level(level)
    where = _between(start, end)
    if level == "campaign":
        return (
            "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            f"{METRICS} FROM campaign WHERE {where}"
        )
    if level == "ad_group":
        return (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status, "
            f"{METRICS} FROM ad_group WHERE {where}"
        )
    # キーワード単位。除外キーワードは実績を持たないが、混ざらないよう条件でも外す
    return (
        "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
        "ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
        "ad_group_criterion.keyword.match_type, ad_group_criterion.status, "
        f"{METRICS} FROM keyword_view WHERE {where} AND ad_group_criterion.negative = FALSE"
    )


def search_terms_query(start: str, end: str) -> str:
    """検索語句 × 広告グループ。期間の合計。"""
    return (
        "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
        "search_term_view.search_term, search_term_view.status, "
        f"{METRICS} FROM search_term_view WHERE {_between(start, end)}"
    )


def keywords_queries() -> Dict[str, str]:
    """キーワードの置き場所は 3 つある。広告グループ、キャンペーン（除外のみ）、共有の除外リスト。"""
    return {
        "ad_group": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
            "ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
            "ad_group_criterion.keyword.match_type, ad_group_criterion.status, "
            "ad_group_criterion.negative "
            "FROM ad_group_criterion "
            "WHERE ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.status != 'REMOVED' "
            "AND ad_group.status != 'REMOVED' AND campaign.status != 'REMOVED'"
        ),
        "campaign": (
            "SELECT campaign.id, campaign.name, campaign_criterion.criterion_id, "
            "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type, "
            "campaign_criterion.negative "
            "FROM campaign_criterion "
            "WHERE campaign_criterion.type = 'KEYWORD' AND campaign.status != 'REMOVED'"
        ),
        "shared_list": (
            "SELECT shared_set.id, shared_set.name, shared_set.type, shared_criterion.criterion_id, "
            "shared_criterion.keyword.text, shared_criterion.keyword.match_type "
            "FROM shared_criterion "
            "WHERE shared_criterion.type = 'KEYWORD' AND shared_set.status = 'ENABLED' "
            "AND shared_set.type IN ('NEGATIVE_KEYWORDS', 'ACCOUNT_LEVEL_NEGATIVE_KEYWORDS')"
        ),
        "shared_list_usage": (
            "SELECT campaign.id, campaign.name, shared_set.id "
            "FROM campaign_shared_set "
            "WHERE campaign_shared_set.status = 'ENABLED' AND campaign.status != 'REMOVED'"
        ),
    }


# ---- 段 2: 広告文・表示オプション・設定・成果の目標・自動生成の文 ----

NOT_REMOVED = "campaign.status != 'REMOVED'"


def ads_query() -> str:
    """削除済み以外の広告。レスポンシブ検索広告の見出し・説明文、有効性、審査の状態。"""
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "ad_group.id, ad_group.name, ad_group.status, "
        "ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.ad_strength, "
        "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.review_status, "
        "ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, "
        "ad_group_ad.ad.responsive_search_ad.descriptions, "
        "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2 "
        "FROM ad_group_ad "
        f"WHERE ad_group_ad.status != 'REMOVED' AND ad_group.status != 'REMOVED' AND {NOT_REMOVED}"
    )


def asset_links_queries() -> Dict[str, str]:
    """表示オプションの付け先は 3 つ。アカウント、キャンペーン、広告グループ。

    絞り込みに使う状態の列は SELECT にも入れる（入れないと API が断る）。
    """
    return {
        "account": (
            "SELECT customer_asset.field_type, customer_asset.status, customer_asset.primary_status, "
            "asset.id, asset.policy_summary.approval_status "
            "FROM customer_asset WHERE customer_asset.status != 'REMOVED'"
        ),
        "campaign": (
            "SELECT campaign.id, campaign.status, campaign_asset.field_type, campaign_asset.status, "
            "campaign_asset.primary_status, asset.id, asset.policy_summary.approval_status "
            f"FROM campaign_asset WHERE campaign_asset.status != 'REMOVED' AND {NOT_REMOVED}"
        ),
        "ad_group": (
            "SELECT campaign.id, campaign.status, ad_group.id, ad_group.status, ad_group_asset.field_type, "
            "ad_group_asset.status, ad_group_asset.primary_status, asset.id, asset.policy_summary.approval_status "
            "FROM ad_group_asset "
            f"WHERE ad_group_asset.status != 'REMOVED' AND ad_group.status != 'REMOVED' AND {NOT_REMOVED}"
        ),
    }


def ad_groups_query() -> str:
    return (
        "SELECT campaign.id, campaign.status, ad_group.id, ad_group.name, ad_group.status "
        f"FROM ad_group WHERE ad_group.status != 'REMOVED' AND {NOT_REMOVED}"
    )


def campaign_settings_query() -> str:
    """いまの設定（入札の種類、地域の判定、1 日の予算）。期間は関係しない。"""
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign.bidding_strategy_type, campaign.geo_target_type_setting.positive_geo_target_type, "
        f"campaign_budget.amount_micros FROM campaign WHERE {NOT_REMOVED}"
    )


def campaign_share_query(start: str, end: str) -> str:
    """期間の実績と、検索で表示できた割合・失った割合。"""
    return (
        "SELECT campaign.id, campaign.status, metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.conversions, metrics.search_impression_share, "
        "metrics.search_rank_lost_impression_share, metrics.search_budget_lost_impression_share "
        f"FROM campaign WHERE {_between(start, end)} AND {NOT_REMOVED}"
    )


def conversion_goals_queries() -> Dict[str, str]:
    """入札が何を成果として数えるか。標準の目標、キャンペーン専用の目標、成果地点そのもの。"""
    return {
        "goals": (
            "SELECT campaign.id, campaign.status, campaign_conversion_goal.category, "
            "campaign_conversion_goal.origin, campaign_conversion_goal.biddable "
            f"FROM campaign_conversion_goal WHERE {NOT_REMOVED}"
        ),
        "goal_config": (
            "SELECT campaign.id, campaign.status, conversion_goal_campaign_config.goal_config_level, "
            "conversion_goal_campaign_config.custom_conversion_goal "
            f"FROM conversion_goal_campaign_config WHERE {NOT_REMOVED}"
        ),
        "custom_goals": (
            "SELECT custom_conversion_goal.resource_name, custom_conversion_goal.name, "
            "custom_conversion_goal.status, custom_conversion_goal.conversion_actions "
            "FROM custom_conversion_goal"
        ),
        "actions": (
            "SELECT conversion_action.resource_name, conversion_action.name, conversion_action.category, "
            "conversion_action.origin, conversion_action.status, conversion_action.primary_for_goal "
            "FROM conversion_action WHERE conversion_action.status != 'REMOVED'"
        ),
    }


def keyword_serving_query() -> str:
    """入札するキーワードと、配信の状態（検索ボリュームが少ない、など）。"""
    return (
        "SELECT campaign.id, campaign.name, campaign.status, ad_group.id, ad_group.name, ad_group.status, "
        "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group_criterion.status, ad_group_criterion.system_serving_status "
        "FROM ad_group_criterion "
        "WHERE ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.negative = FALSE "
        f"AND ad_group_criterion.status != 'REMOVED' AND ad_group.status != 'REMOVED' AND {NOT_REMOVED}"
    )


def generated_text_queries(start: str, end: str) -> Dict[str, str]:
    """Google が自動で作った文と、その着地先。期間の合計。

    texts は、期間に表示があった見出し・説明文を出どころで絞らずに読む。Google が広告に足した文は、
    アセットそのものの出どころ（asset.source）は ADVERTISER のまま、つながりの側の出どころ
    （ad_group_ad_asset_view.source）だけが AUTOMATICALLY_CREATED になることがある（2026-11-24 実物で確認）。
    GAQL では OR が使えないので、両方の列を読んで、どちらかが自動で作成なら Google が足した文として分ける。
    入稿した文も、組み合わせの見出しの作り手を見分けるのに使う。
    """
    where = _between(start, end)
    return {
        "texts": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, asset.id, "
            "ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
            "ad_group_ad_asset_view.source, "
            f"asset.text_asset.text, asset.source, {METRICS} "
            "FROM ad_group_ad_asset_view "
            f"WHERE ad_group_ad_asset_view.field_type IN ('HEADLINE', 'DESCRIPTION') AND {where}"
        ),
        "combinations": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
            "ai_max_search_term_ad_combination_view.search_term, "
            "ai_max_search_term_ad_combination_view.headline, "
            f"ai_max_search_term_ad_combination_view.landing_page, {METRICS} "
            f"FROM ai_max_search_term_ad_combination_view WHERE {where}"
        ),
        "landing_pages": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
            f"landing_page_view.unexpanded_final_url, segments.landing_page_source, {METRICS} "
            f"FROM landing_page_view WHERE {where}"
        ),
        # 広げた着地ページ（実際にクリックが向かった URL）と、そのとき一緒に出た文（2026-11-24 足した）
        "expanded_pages": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
            f"expanded_landing_page_view.expanded_final_url, {METRICS} "
            f"FROM expanded_landing_page_view WHERE {where}"
        ),
        "expansion_texts": (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, asset.id, asset.text_asset.text, "
            "final_url_expansion_asset_view.field_type, final_url_expansion_asset_view.final_url, "
            f"{METRICS} FROM final_url_expansion_asset_view WHERE {where}"
        ),
    }


# ---- 段 3: 配信量の見込み、知見カードとの照らし合わせ ----

def keyword_share_query(start: str, end: str) -> str:
    """キーワードごとの実績と、表示される割合。配信量の見込みの材料。"""
    return (
        "SELECT campaign.id, campaign.advertising_channel_type, ad_group.id, "
        "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.search_impression_share "
        f"FROM keyword_view WHERE {_between(start, end)} AND ad_group_criterion.negative = FALSE "
        "AND metrics.impressions > 0"
    )


def campaign_state_query() -> str:
    """キャンペーンの種類・入札・AI Max の有無。知見カードと照らす材料。"""
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        f"campaign.bidding_strategy_type, campaign.ai_max_setting.enable_ai_max FROM campaign WHERE {NOT_REMOVED}"
    )


# ---- 段 3.6: 変更の履歴、変更の前後、いつもの水準からの外れ（どれも読むだけ） ----

CHANGE_EVENT_DAYS = 30      # change_event は過去 30 日まで（公式の change-event の手引き。27 の 27-28）
CHANGE_EVENT_MAX_LIMIT = 10_000  # change_event は LIMIT が必須で、10,000 行まで（同じ手引き）

DAILY_LEVELS = {"campaign": "campaign", "キャンペーン": "campaign", "ad_group": "ad_group", "adgroup": "ad_group",
                "広告グループ": "ad_group", "account": "account", "アカウント": "account"}


def normalize_daily_level(level: str, allowed=("campaign", "ad_group", "account")) -> str:
    key = str(level).strip().lower()
    picked = DAILY_LEVELS.get(key)
    if picked not in allowed:
        raise ValueError(f"単位は {' / '.join(allowed)} のどれか（日本語でも可。受け取った値: {level!r}）")
    return picked


def normalize_campaign_id(campaign_id) -> str:
    """キャンペーン ID は数字だけ。空なら絞らない。"""
    s = re.sub(r"\s", "", str(campaign_id or ""))
    if s and not re.fullmatch(r"\d{1,20}", s):
        raise ValueError(f"キャンペーン ID は数字で指定する（受け取った値: {campaign_id!r}）")
    return s


def change_events_query(start: str, end_next: str, limit: int) -> str:
    """期間の変更の履歴。新しい順。だれが変えたか（メールアドレス）は選ばない。

    end_next は終了日の翌日（公式の例と同じく日付だけで区切り、終了日の分を取りこぼさない）。
    """
    validate_period(start, end_next)
    n = int(limit)
    if not 1 <= n <= CHANGE_EVENT_MAX_LIMIT:
        raise ValueError(f"change_event の LIMIT は 1〜{CHANGE_EVENT_MAX_LIMIT:,}")
    return (
        "SELECT change_event.change_date_time, change_event.change_resource_type, "
        "change_event.change_resource_name, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, change_event.old_resource, "
        "change_event.new_resource, change_event.campaign, change_event.ad_group "
        f"FROM change_event WHERE change_event.change_date_time >= '{start}' "
        f"AND change_event.change_date_time <= '{end_next}' "
        f"ORDER BY change_event.change_date_time DESC LIMIT {n}"
    )


def campaign_names_query() -> str:
    """キャンペーンの ID と名前（削除済みも含む。変更の履歴に名前を付けるため）。"""
    return "SELECT campaign.id, campaign.name, campaign.status FROM campaign"


def daily_query(level: str, start: str, end: str, campaign_id: str = "") -> str:
    """日別の実績。キャンペーン単位は 1 日の予算（いまの額）も一緒に読む。"""
    where = _between(start, end)
    cid = normalize_campaign_id(campaign_id)
    if cid:
        where += f" AND campaign.id = {cid}"
    if level == "ad_group":
        return (
            "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status, segments.date, "
            f"{METRICS} FROM ad_group WHERE {where}"
        )
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        f"campaign_budget.amount_micros, segments.date, {METRICS} FROM campaign WHERE {where}"
    )


DETAIL_KINDS = {"ad_group": "広告グループ", "ad": "広告", "keyword": "キーワード", "search_term": "検索語句"}


def detail_daily_query(kind: str, start: str, end: str) -> str:
    """広告グループ・広告・キーワード・検索語句の日別の実績（レポートのスプレッドシートの元の行）。読むだけ。"""
    if kind not in DETAIL_KINDS:
        raise ValueError(f"階層は {' / '.join(DETAIL_KINDS)} のどれか（受け取った値: {kind!r}）")
    where = _between(start, end)
    head = "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, segments.date, "
    if kind == "ad_group":
        return f"{head}{METRICS} FROM ad_group WHERE {where}"
    if kind == "ad":
        return (f"{head}ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.responsive_search_ad.headlines, "
                f"{METRICS} FROM ad_group_ad WHERE {where}")
    if kind == "keyword":
        return (f"{head}ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
                f"ad_group_criterion.keyword.match_type, {METRICS} FROM keyword_view "
                f"WHERE {where} AND ad_group_criterion.negative = FALSE")
    return f"{head}search_term_view.search_term, {METRICS} FROM search_term_view WHERE {where}"
