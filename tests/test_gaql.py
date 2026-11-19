"""GAQL の組み立て。API は呼ばない。"""
import pytest

from ochinana_ads import gaql


def test_許可したアカウントだけ通す():
    assert gaql.normalize_customer_id("1112223333") == "1112223333"
    assert gaql.normalize_customer_id("111-222-3333") == "1112223333"
    assert gaql.normalize_customer_id(" 4445556666 ") == "4445556666"


@pytest.mark.parametrize("bad", ["1234567890", "0000000000", "9999999999"])
def test_許可リストに無いアカウントは断る(bad):
    with pytest.raises(ValueError, match="許可リストに無い"):
        gaql.normalize_customer_id(bad)


@pytest.mark.parametrize("bad", ["", "abc", "53233542", "1112223333'; DROP", "customers/1112223333"])
def test_形の違うアカウントIDは断る(bad):
    with pytest.raises(ValueError):
        gaql.normalize_customer_id(bad)


def test_期間はそのまま返す():
    assert gaql.validate_period("2026-10-01", "2026-10-31") == ("2026-10-01", "2026-10-31")
    assert gaql.validate_period("2026-11-10", "2026-11-10") == ("2026-11-10", "2026-11-10")


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026/10/01", "2026-10-31"),
        ("2026-10-01", "20261031"),
        ("2026-02-30", "2026-03-01"),
        ("2026-10-31", "2026-10-01"),
        ("2026-10-01' OR '1'='1", "2026-10-31"),
        ("LAST_30_DAYS", "2026-10-31"),
    ],
)
def test_おかしな期間は断る(start, end):
    with pytest.raises(ValueError):
        gaql.validate_period(start, end)


def test_単位は日本語でも英語でも受ける():
    assert gaql.normalize_level("campaign") == "campaign"
    assert gaql.normalize_level("キャンペーン") == "campaign"
    assert gaql.normalize_level("Ad_Group") == "ad_group"
    assert gaql.normalize_level("広告グループ") == "ad_group"
    assert gaql.normalize_level("キーワード") == "keyword"
    with pytest.raises(ValueError):
        gaql.normalize_level("ad")


@pytest.mark.parametrize(
    "level,resource", [("campaign", "FROM campaign "), ("ad_group", "FROM ad_group "), ("keyword", "FROM keyword_view ")]
)
def test_実績の問い合わせ(level, resource):
    q = gaql.performance_query(level, "2026-10-01", "2026-10-31")
    assert resource in q
    assert "segments.date BETWEEN '2026-10-01' AND '2026-10-31'" in q
    for m in ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"):
        assert m in q
    # 日別に割らない（期間の合計で返す）
    assert "segments.date" not in q.split("FROM")[0]


def test_キーワード単位の実績は除外キーワードを外す():
    q = gaql.performance_query("keyword", "2026-10-01", "2026-10-31")
    assert "ad_group_criterion.negative = FALSE" in q
    assert "ad_group_criterion.keyword.match_type" in q


def test_実績の問い合わせは期間を確かめてから組む():
    with pytest.raises(ValueError):
        gaql.performance_query("campaign", "2026-10-31", "2026-10-01")
    with pytest.raises(ValueError):
        gaql.search_terms_query("x", "2026-10-01")


def test_検索語句の問い合わせ():
    q = gaql.search_terms_query("2026-10-12", "2026-11-08")
    assert "FROM search_term_view" in q
    assert "search_term_view.search_term" in q
    assert "search_term_view.status" in q
    assert "BETWEEN '2026-10-12' AND '2026-11-08'" in q


def test_キーワードの問い合わせは除外の欄を必ず取る():
    q = gaql.keywords_queries()
    assert set(q) == {"ad_group", "campaign", "shared_list", "shared_list_usage"}
    assert "ad_group_criterion.negative" in q["ad_group"].split("FROM")[0]
    assert "campaign_criterion.negative" in q["campaign"].split("FROM")[0]
    assert "ad_group_criterion.type = 'KEYWORD'" in q["ad_group"]
    assert "NEGATIVE_KEYWORDS" in q["shared_list"]
    # 構成を読むだけなので、実績や日付は入れない
    for text in q.values():
        assert "metrics." not in text and "segments." not in text


def test_地域と言語の名前の問い合わせは形を確かめる():
    assert "geoTargetConstants/2392" in gaql.geo_name_query("geoTargetConstants/2392")
    assert "languageConstants/1005" in gaql.language_name_query("languageConstants/1005")
    with pytest.raises(ValueError):
        gaql.geo_name_query("geoTargetConstants/2392' OR 1=1")
    with pytest.raises(ValueError):
        gaql.language_name_query("2392")


def test_どの問い合わせも読み取りの文だけ():
    qs = [
        gaql.account_query(),
        gaql.targeting_query(),
        gaql.search_terms_query("2026-10-01", "2026-10-31"),
        *[gaql.performance_query(lv, "2026-10-01", "2026-10-31") for lv in ("campaign", "ad_group", "keyword")],
        *gaql.keywords_queries().values(),
    ]
    for q in qs:
        assert q.startswith("SELECT ")
        assert ";" not in q
