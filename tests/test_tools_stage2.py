"""段 2 の道具の返答の形を、にせの API の行で確かめる。本物の API にも外にもつながない。"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import config, gaql, tools


def E(name):
    return NS(name=name)


def M(imp=0, clicks=0, yen=0, conv=0.0, **extra):
    return NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, conversions=conv, conversions_value=0.0, **extra)


CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))
CAMP = dict(id=1, name="検索A", status=E("ENABLED"), advertising_channel_type=E("SEARCH"))
PAUSED = dict(id=2, name="止めている", status=E("PAUSED"), advertising_channel_type=E("SEARCH"))
GROUP = NS(id=10, name="ルール", status=E("ENABLED"))


def policy(status="APPROVED"):
    return NS(approval_status=E(status), review_status=E("REVIEWED"))


def api_rows():
    def campaign_row(c, imp, clicks, yen, rank, budget):
        return NS(
            campaign=NS(**c, bidding_strategy_type=E("MAXIMIZE_CONVERSIONS"),
                        geo_target_type_setting=NS(positive_geo_target_type=E("PRESENCE_OR_INTEREST"))),
            campaign_budget=NS(amount_micros=1_500_000_000),
            metrics=M(imp, clicks, yen, search_impression_share=0.2, search_rank_lost_impression_share=rank,
                      search_budget_lost_impression_share=budget),
        )

    ad = NS(
        campaign=NS(**CAMP), ad_group=GROUP,
        ad_group_ad=NS(
            status=E("ENABLED"), ad_strength=E("POOR"), policy_summary=policy(),
            ad=NS(id=777, type_=E("RESPONSIVE_SEARCH_AD"), final_urls=["https://example.com/rule/?utm_source=google"],
                  responsive_search_ad=NS(headlines=[NS(text=f"ホッケーのルール {i}") for i in range(3)],
                                          descriptions=[NS(text="説明")] * 2, path1="ホッケー", path2="")),
        ),
    )
    criterion = lambda text, negative=False, serving="ELIGIBLE": NS(  # noqa: E731
        campaign=NS(**CAMP), ad_group=GROUP,
        ad_group_criterion=NS(keyword=NS(text=text, match_type=E("BROAD")), status=E("ENABLED"),
                              negative=negative, system_serving_status=E(serving)))
    return {
        "customer": [CUSTOMER],
        "campaign": [campaign_row(CAMP, 200, 8, 9919, 0.57, 0.25), campaign_row(PAUSED, 0, 0, 0, 0.0, 0.0)],
        "conversion_action": [NS(conversion_action=NS(resource_name="a/1", name="保存", category=E("PAGE_VIEW"),
                                                      origin=E("WEBSITE"), status=E("ENABLED"), primary_for_goal=True))],
        "custom_conversion_goal": [],
        "conversion_goal_campaign_config": [NS(campaign=NS(id=1), conversion_goal_campaign_config=NS(
            goal_config_level=E("CUSTOMER"), custom_conversion_goal=""))],
        "campaign_conversion_goal": [NS(campaign=NS(id=1), campaign_conversion_goal=NS(
            category=E("PAGE_VIEW"), origin=E("WEBSITE"), biddable=True))],
        "ad_group": [NS(campaign=NS(**CAMP), ad_group=GROUP)],
        "ad_group_ad": [ad],
        "customer_asset": [],
        "campaign_asset": [NS(campaign=NS(**CAMP), campaign_asset=NS(field_type=E("SITELINK"), status=E("ENABLED"),
                                                                     primary_status=E("ELIGIBLE")),
                              asset=NS(id=i, policy_summary=policy())) for i in range(6)],
        "ad_group_asset": [NS(campaign=NS(**CAMP), ad_group=GROUP,
                              ad_group_asset=NS(field_type=E("AD_IMAGE"), status=E("ENABLED"), primary_status=E("PENDING")),
                              asset=NS(id=99, policy_summary=policy("UNSPECIFIED")))],
        "ad_group_criterion": [criterion("ホッケー ルール"), criterion("ルール ホッケー"), criterion("グローブ", negative=True)],
        "campaign_criterion": [],
        "shared_criterion": [],
        "campaign_shared_set": [],
        "search_term_view": [
            NS(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name="ルール"),
               search_term_view=NS(search_term=t, status=E("NONE")), metrics=M(imp, clicks, yen))
            for t, imp, clicks, yen in [("ホッケー ルール", 60, 8, 9919), ("live sports", 30, 0, 0), ("ホッケー グローブ", 20, 0, 0)]
        ],
        "ad_group_ad_asset_view": [],
        "ai_max_search_term_ad_combination_view": [],
        "landing_page_view": [],
        "expanded_landing_page_view": [],
        "final_url_expansion_asset_view": [],
    }


def fake_run(rows, seen=None):
    def run(customer_id, query):
        assert query.startswith("SELECT ")
        if seen is not None:
            seen.append(query)
        resource = query.split(" FROM ")[1].split()[0]
        out = rows[resource]
        if resource == "ad_group_criterion" and "negative = FALSE" in query:
            out = [r for r in out if not r.ad_group_criterion.negative]
        return out
    return run


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    yield
    tools._accounts.clear()


def test_広告文と表示オプション():
    out = tools.get_ads_and_assets("1112223333", run=fake_run(api_rows()))
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["件数"] == {"表示しているキャンペーン": 1, "一時停止で省いたキャンペーン": 1, "表示している広告": 1}
    camp = out["キャンペーン"][0]
    assert camp["表示オプション（キャンペーン単位）"]["サイトリンク"] == {"件数": 6, "状態": {"配信可能": 6}}
    assert camp["表示オプション（キャンペーン単位）"]["コールアウト"] == {"件数": 0}  # 無い種類も 0 件として出す
    group = camp["広告グループ"][0]
    assert group["表示オプション（広告グループ単位）"]["画像"] == {"件数": 1, "状態": {"審査中": 1}}
    ad = group["広告"][0]
    assert ad["有効性"] == "低い" and ad["審査"] == "承認済み（審査済み）"
    assert ad["見出しの本数"] == 3 and ad["説明文の本数"] == 2
    assert ad["見出し"][0] == "ホッケーのルール 0" and ad["表示パス"] == "ホッケー"
    assert set(out["表示オプション（アカウント単位）"]) == {"サイトリンク", "コールアウト", "構造化スニペット", "画像"}


def test_広告文は本数だけにもできる_一時停止も含められる():
    out = tools.get_ads_and_assets("1112223333", only_enabled=False, include_text=False, run=fake_run(api_rows()))
    assert out["件数"]["表示しているキャンペーン"] == 2
    ad = next(c for c in out["キャンペーン"] if c["キャンペーン"] == "検索A")["広告グループ"][0]["広告"][0]
    assert "見出し" not in ad and ad["見出しの本数"] == 3


def test_自動で作られた文が無いときはまだ無いと返す():
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(api_rows()))
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["自動で作られた見出し・説明文"].startswith("まだ無い")
    assert out["見出しと着地 URL の組み合わせ"].startswith("まだ無い")
    assert out["着地 URL"].startswith("まだ無い")
    assert out["件数"]["自動で作られた見出し・説明文"] == 0
    assert out["広げた着地ページと、一緒に出た文"]["広げた着地ページ"].startswith("まだ無い")
    assert out["件数"]["広げた着地ページ"] == 0


def test_自動で作られた文があるとき():
    rows = api_rows()
    base = dict(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name="ルール"))
    rows["ad_group_ad_asset_view"] = [NS(**base, ad_group_ad_asset_view=NS(field_type=E("HEADLINE"), performance_label=E("GOOD"),
                                                                           source=E("AUTOMATICALLY_CREATED")),
                                         asset=NS(text_asset=NS(text="6人制ホッケーの見どころ"), source=E("AUTOMATICALLY_CREATED")),
                                         metrics=M(40, 3, 900))]
    rows["ai_max_search_term_ad_combination_view"] = [
        NS(**base, ai_max_search_term_ad_combination_view=NS(search_term=t, headline="6人制ホッケーの見どころ",
                                                             landing_page="https://example.com/teams/"), metrics=M(imp, 1, 300))
        for t, imp in [("ホッケー 6人制", 10), ("ホッケー teams", 5)]]
    rows["landing_page_view"] = [NS(**base, landing_page_view=NS(unexpanded_final_url="https://example.com/teams/"),
                                    segments=NS(landing_page_source=E("AUTOMATIC")), metrics=M(15, 2, 600))]
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    text = out["自動で作られた見出し・説明文"][0]
    assert text["文"] == "6人制ホッケーの見どころ" and text["種類"] == "見出し" and text["評価"] == "良"
    assert text["費用（円）"] == 900
    combo = out["見出しと着地 URL の組み合わせ"]
    assert len(combo) == 1 and combo[0]["表示回数"] == 15 and combo[0]["着地 URL"] == "https://example.com/teams/"
    assert out["着地 URL"][0]["選ばれ方"] == "Google が自動で選択"
    assert out["件数"]["うち Google が自動で選んだ着地 URL"] == 1
    assert "micros" not in json.dumps(out, ensure_ascii=False)


def test_点検の実行(tmp_path):
    seen, audited = [], []

    def audit(url):
        audited.append(url)
        return {"URL": url, "表示速度（スマホ）": {"測れたか": True, "総合点（100 点満点）": 60, "最初の表示（秒）": 2.8,
                                           "メイン画像の表示（秒）": 5.1, "描画を止めている資源（上位）": [], "計測の条件": "スマホ"}}

    out = tools.run_checks("1112223333", "2026-05-01", "2026-11-10", run=fake_run(api_rows(), seen), audit=audit)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["点検した範囲"] == {"有効なキャンペーン": ["検索A"], "一時停止で対象外にしたキャンペーンの数": 1}
    assert [r["点検"] for r in out["点検の一覧"]] == [f"C{i:02d}" for i in range(1, 11)]
    assert audited == ["https://example.com/rule/"]  # 計測用の ? 以降は外して測る
    by = {}
    for f in out["所見"]:
        by.setdefault(f["点検"], []).append(f)
        assert f["誰の意見"] == "AI の提案（未承認）"
    # 成果 0 件、広告文が足りない、表示オプションが無い、語順違いの重複、クリック 0 の語句、地域、LP が遅い、失った表示
    assert set(by) == {"C01", "C02", "C03", "C05", "C06", "C08", "C09", "C10"}
    assert [c["検索語句"] for c in by["C06"][0]["根拠"]["候補（表示の多い順）"]] == ["live sports"]  # グローブは除外済み
    weights = [f["重さ"] for f in out["所見"]]
    assert weights == sorted(weights, key={"大": 0, "中": 1, "小": 2}.get)
    saved = json.loads(Path(out["全部の結果の保存先"]).read_text(encoding="utf-8"))
    assert Path(out["全部の結果の保存先"]).parent == tmp_path
    assert len(saved) >= len(out["所見"])
    assert all(q.startswith("SELECT ") for q in seen)


def test_点検はLPの計測を省ける():
    def audit(url):
        raise AssertionError("測ってはいけない")

    out = tools.run_checks("1112223333", "2026-05-01", "2026-11-10", include_lp=False, run=fake_run(api_rows()), audit=audit)
    c09 = [r for r in out["判定できなかったもの"] if r["点検"] == "C09"]
    assert len(c09) == 1 and "省いた" in c09[0]["理由"]


def test_LPは3本まで測り_測れなかったものは判定できないにする():
    rows = api_rows()
    ad = rows["ad_group_ad"][0].ad_group_ad.ad
    ad.final_urls = ["https://example.com/a/", "https://example.com/b/", "https://example.com/c/", "https://example.com/d/"]
    called = []

    def audit(url):
        called.append(url)
        return {"URL": url, "表示速度（スマホ）": {"測れたか": False, "理由": "PageSpeed Insights: HTTP 429。手元の Lighthouse: 入っていない"}}

    out = tools.run_checks("1112223333", "2026-05-01", "2026-11-10", run=fake_run(rows), audit=audit)
    assert called == ["https://example.com/a/", "https://example.com/b/", "https://example.com/c/"]
    c09 = [r for r in out["判定できなかったもの"] if r["点検"] == "C09"]
    assert len(c09) == 3 and all("429" in r["理由"] and "Lighthouse" in r["理由"] for r in c09)
    assert "4 本のうち先頭の 3 本" in out["注記"][0]


def test_点検も許可していないアカウントと逆さまの期間は断る():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    with pytest.raises(ValueError, match="許可リスト"):
        tools.run_checks("1234567890", "2026-05-01", "2026-11-10", run=run)
    with pytest.raises(ValueError, match="より後"):
        tools.run_checks("1112223333", "2026-11-10", "2026-05-01", run=run)
    with pytest.raises(ValueError, match="許可リスト"):
        tools.get_ads_and_assets("1234567890", run=run)
    with pytest.raises(ValueError):
        tools.get_generated_text("1112223333", "2026/05/01", "2026-11-10", run=run)


def test_段2の問い合わせ文():
    qs = [gaql.ads_query(), gaql.ad_groups_query(), gaql.campaign_settings_query(), gaql.keyword_serving_query(),
          gaql.campaign_share_query("2026-10-01", "2026-10-31"),
          *gaql.asset_links_queries().values(), *gaql.conversion_goals_queries().values(),
          *gaql.generated_text_queries("2026-10-01", "2026-10-31").values()]
    for q in qs:
        assert q.startswith("SELECT ") and ";" not in q
    # 状態で絞る問い合わせは、その列を SELECT にも入れる（API の決まり）
    links = gaql.asset_links_queries()
    assert "campaign.status" in links["campaign"].split(" FROM ")[0]
    assert "ad_group.status" in links["ad_group"].split(" FROM ")[0]
    gen = gaql.generated_text_queries("2026-10-01", "2026-10-31")
    # Google が足した文は asset.source が ADVERTISER のままのことがあるので、出どころで絞らず両方の列を読む
    assert "WHERE asset.source" not in gen["texts"]
    assert "ad_group_ad_asset_view.source" in gen["texts"].split(" FROM ")[0]
    assert "asset.source" in gen["texts"].split(" FROM ")[0]
    assert all("BETWEEN '2026-10-01' AND '2026-10-31'" in q for q in gen.values())
    assert "campaign_conversion_goal.biddable" in gaql.conversion_goals_queries()["goals"]
    assert "custom_conversion_goal.conversion_actions" in gaql.conversion_goals_queries()["custom_goals"]
    assert "search_rank_lost_impression_share" in gaql.campaign_share_query("2026-10-01", "2026-10-31")
    assert "system_serving_status" in gaql.keyword_serving_query()
    with pytest.raises(ValueError):
        gaql.generated_text_queries("2026-10-31", "2026-10-01")


def _text_row(text, link, asset, imp=10, field="HEADLINE", group="ルール"):
    return NS(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name=group),
              ad_group_ad_asset_view=NS(field_type=E(field), performance_label=E("PENDING"), source=E(link)),
              asset=NS(text_asset=NS(text=text), source=E(asset)), metrics=M(imp, 1, 100))


def test_アセットの出どころが広告主でも_つながりの側が自動なら_Googleが足した文として拾う():
    rows = api_rows()
    rows["ad_group_ad_asset_view"] = [
        _text_row("観戦ガイド", "ADVERTISER", "ADVERTISER"),
        _text_row("初心者でも楽しめる", "ADVERTISER", "ADVERTISER"),
        _text_row("ルールが分かる観戦の手引き", "AUTOMATICALLY_CREATED", "ADVERTISER"),  # 2026-11-24 実物で見た形
        _text_row("無料で読める", "ADVERTISER", "AUTOMATICALLY_CREATED", field="DESCRIPTION"),
    ]
    base = dict(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name="ルール"))
    rows["ai_max_search_term_ad_combination_view"] = [
        NS(**base, ai_max_search_term_ad_combination_view=NS(search_term="q", headline=h, landing_page="https://example.com/"),
           metrics=M(5, 0, 0))
        for h in ["観戦ガイド | 初心者でも楽しめる", "ルールが分かる観戦の手引き | 観戦ガイド", "どこにも無い見出し"]]
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    got = {t["文"]: t for t in out["自動で作られた見出し・説明文"]}
    assert set(got) == {"ルールが分かる観戦の手引き", "無料で読める"}
    assert got["ルールが分かる観戦の手引き"]["つながりの出どころ"] == "自動で作成"
    assert got["ルールが分かる観戦の手引き"]["アセットの出どころ"] == "広告主"
    assert got["無料で読める"]["アセットの出どころ"] == "自動で作成"
    assert out["件数"]["自動で作られた見出し・説明文"] == 2
    assert out["件数"]["入稿した見出し・説明文（表示があったもの）"] == 2
    origin = {c["見出し"]: c["見出しの作り手"] for c in out["見出しと着地 URL の組み合わせ"]}
    assert origin["観戦ガイド | 初心者でも楽しめる"] == tools.ORIGIN_ADVERTISER
    assert origin["ルールが分かる観戦の手引き | 観戦ガイド"] == tools.ORIGIN_GOOGLE
    assert origin["どこにも無い見出し"] == tools.ORIGIN_OTHER
    text = json.dumps(out, ensure_ascii=False)
    assert "ここに出る文は Google が自動で作ったもの" not in text  # 組み合わせの欄に付けていた誤った断り
    assert "つながりの側だけに印が付く" in out["文の出どころの読み方"]["自動で作られた見出し・説明文"]


def test_入稿した文を読めない広告グループの組み合わせは_見分けられないと返す():
    rows = api_rows()
    rows["ai_max_search_term_ad_combination_view"] = [
        NS(campaign=NS(id=1, name="検索A"), ad_group=NS(id=11, name="別"),
           ai_max_search_term_ad_combination_view=NS(search_term="q", headline="何か", landing_page="https://example.com/"),
           metrics=M(5, 0, 0))]
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    assert out["見出しと着地 URL の組み合わせ"][0]["見出しの作り手"] == tools.ORIGIN_UNKNOWN


def test_Googleが足した文の区切りが全角でも半角でも_同じ文として見分ける():
    rows = api_rows()
    rows["ad_group_ad_asset_view"] = [
        _text_row("観戦ガイド", "ADVERTISER", "ADVERTISER"),
        _text_row("前半 | 後半", "AUTOMATICALLY_CREATED", "ADVERTISER"),  # 文では半角の「 | 」
    ]
    rows["ai_max_search_term_ad_combination_view"] = [
        NS(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name="ルール"),
           ai_max_search_term_ad_combination_view=NS(search_term="q", headline="観戦ガイド | 前半｜後半",  # 組み合わせでは全角の「｜」
                                                     landing_page="https://example.com/"), metrics=M(5, 0, 0))]
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    assert out["見出しと着地 URL の組み合わせ"][0]["見出しの作り手"] == tools.ORIGIN_GOOGLE


def test_広げた着地ページと一緒に出た文():
    rows = api_rows()
    base = dict(campaign=NS(id=1, name="検索A"), ad_group=NS(id=10, name="ルール"))
    rows["expanded_landing_page_view"] = [
        NS(**base, expanded_landing_page_view=NS(expanded_final_url=u), metrics=M(imp, 1, 100))
        for u, imp in [("https://example.com/a/", 7), ("https://example.com/b/?utm_source=google", 3), ("https://example.com/a/", 2)]]
    rows["final_url_expansion_asset_view"] = [
        NS(**base, asset=NS(text_asset=NS(text="広げた先の見出し")),
           final_url_expansion_asset_view=NS(field_type=E("HEADLINE"), final_url="https://example.com/a/"), metrics=M(4, 0, 0))]
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    e = out["広げた着地ページと、一緒に出た文"]
    pages = {p["クリックが向かった URL"]: p for p in e["広げた着地ページ"]}
    assert pages["https://example.com/a/"]["表示回数"] == 9 and pages["https://example.com/a/"]["URL の ? 以降"] == "なし"
    assert pages["https://example.com/b/?utm_source=google"]["URL の ? 以降"] == "あり"
    assert e["一緒に出た文"][0]["文"] == "広げた先の見出し" and e["一緒に出た文"][0]["種類"] == "見出し"
    assert out["件数"]["広げた着地ページ"] == 2 and out["件数"]["広げた着地ページと一緒に出た文"] == 1
    q = gaql.generated_text_queries("2026-10-01", "2026-10-31")
    assert "FROM expanded_landing_page_view" in q["expanded_pages"] and "FROM final_url_expansion_asset_view" in q["expansion_texts"]


def test_広げた着地ページが読めなくても_ほかの欄は返す():
    rows = api_rows()
    del rows["final_url_expansion_asset_view"]  # にせの API が KeyError を出す
    out = tools.get_generated_text("1112223333", "2026-10-01", "2026-10-31", run=fake_run(rows))
    assert out["広げた着地ページと、一緒に出た文"]["広げた着地ページ"].startswith("読めなかった")
    assert out["件数"]["広げた着地ページ"] == "読めなかった"
    assert out["自動で作られた見出し・説明文"].startswith("まだ無い")
