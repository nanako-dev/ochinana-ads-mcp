"""点検 10 個の判定を、作り物のデータで試す。API は呼ばない。"""
import copy

import pytest

from ochinana_ads import checks

CAMP_ID, G1, G2 = 1, 10, 20
ACTION = "customers/1/conversionActions/9"


def camp(**over):
    base = {"id": CAMP_ID, "name": "検索A", "status": "ENABLED", "channel": "SEARCH",
            "bidding": "MAXIMIZE_CONVERSIONS", "geo_type": "PRESENCE", "budget_micros": 1_500_000_000,
            "impressions": 1000, "clicks": 50, "cost_micros": 30_000_000_000, "conversions": 4.0,
            "search_is": 0.80, "rank_lost": 0.05, "budget_lost": 0.05}
    return {**base, **over}


def ad(group=G1, **over):
    base = {"campaign_id": CAMP_ID, "ad_group_id": group, "ad_id": 100 + group, "type": "RESPONSIVE_SEARCH_AD",
            "status": "ENABLED", "strength": "GOOD", "approval": "APPROVED", "review": "REVIEWED",
            "headlines": [f"ホッケーのルール {i}" for i in range(15)], "descriptions": ["説明"] * 4,
            "final_urls": ["https://example.com/handball/"], "path1": "ホッケー", "path2": "ルール"}
    return {**base, **over}


def kw(text, group=G1, match="BROAD", status="ENABLED", serving="ELIGIBLE"):
    return {"campaign_id": CAMP_ID, "ad_group_id": group, "text": text, "match": match, "status": status, "serving": serving}


def link(field_type, level="campaign", group=None, status="ENABLED"):
    return {"level": level, "campaign_id": None if level == "account" else CAMP_ID, "ad_group_id": group,
            "field_type": field_type, "status": status, "primary_status": "ELIGIBLE", "approval": "APPROVED"}


def term(text, imp, clicks=0, group=G1, status="NONE"):
    return {"term": text, "campaign_id": CAMP_ID, "ad_group_id": group, "status": status,
            "impressions": imp, "clicks": clicks, "cost_micros": clicks * 100_000_000, "conversions": 0.0, "conversions_value": 0.0}


def setup(biddable=True, action_status="ENABLED", custom=False):
    s = {"actions": {ACTION: {"name": "保存ボタン", "category": "PAGE_VIEW", "origin": "WEBSITE",
                              "status": action_status, "primary": True}},
         "custom_goals": {}, "config": {CAMP_ID: {"level": "CUSTOMER", "custom_goal": None}},
         "goals": [{"campaign_id": CAMP_ID, "category": "PAGE_VIEW", "origin": "WEBSITE", "biddable": biddable}]}
    if custom:
        s["custom_goals"]["g/1"] = {"name": "専用の目標", "status": "ENABLED", "actions": [ACTION]}
        s["config"][CAMP_ID] = {"level": "CAMPAIGN", "custom_goal": "g/1"}
        s["goals"] = []
    return s


def healthy(**over):
    """どの点検にも引っかからない状態。ここから 1 か所だけ崩して試す。"""
    snap = {
        "campaigns": [camp()],
        "conversion_setup": setup(),
        "ad_groups": [{"campaign_id": CAMP_ID, "id": G1, "name": "ルール", "status": "ENABLED"}],
        "ads": [ad()],
        "asset_links": [link("SITELINK")] * 6 + [link("CALLOUT")] * 4 + [link("STRUCTURED_SNIPPET")]
                       + [link("AD_IMAGE", "ad_group", G1)] * 3,
        "keywords": [kw("ホッケー ルール"), kw("ホッケー 初心者", match="PHRASE")],
        "negatives": [],
        "search_terms": [term("ホッケー ルール", 40, 5), term("ホッケー とは", 2)],
        "lp_audits": [lp_audit()],
    }
    return {**snap, **over}


def lp_audit(lcp=2.0, fcp=1.2, ok=True, reason=None):
    if not ok:
        return {"URL": "https://example.com/", "表示速度（スマホ）": {"測れたか": False, "理由": reason}}
    return {"URL": "https://example.com/", "表示速度（スマホ）": {
        "測れたか": True, "測り方": "手元の Lighthouse", "総合点（100 点満点）": 90, "最初の表示（秒）": fcp, "メイン画像の表示（秒）": lcp,
        "描画を止めている資源（上位）": [{"資源": "https://fonts.googleapis.com/css2", "止めている時間（ミリ秒）": 900, "大きさ（KB）": 2.0}],
        "計測の条件": "スマホ"}}


def run(check_id, snap):
    c = next(c for c in checks.CHECKS if c.id == check_id)
    return c.fn(snap)


def statuses(results):
    return [r["結果"] for r in results]


def one_finding(results):
    found = [r for r in results if r["結果"] == "所見あり"]
    assert len(found) == 1, results
    return found[0]


# ---- 全体 ----

def test_点検は10個でIDと版がある():
    assert [c.id for c in checks.CHECKS] == [f"C{i:02d}" for i in range(1, 11)]
    assert all(c.version and c.name for c in checks.CHECKS)


def test_健康な状態は所見なし():
    results = checks.run_all(healthy())
    assert set(statuses(results)) == {"問題なし"}, [r for r in results if r["結果"] != "問題なし"]
    assert {r["点検"] for r in results} == {c.id for c in checks.CHECKS}


def test_材料が無いときは黙って合格にしない():
    empty = {"campaigns": [], "conversion_setup": None, "ad_groups": [], "ads": [], "asset_links": [],
             "keywords": [], "negatives": [], "search_terms": [], "lp_audits": None}
    results = checks.run_all(empty)
    assert len(results) == 10
    assert set(statuses(results)) == {"判定できない"}
    assert all(r["理由"] for r in results)


def test_一時停止のキャンペーンは点検しない():
    results = checks.run_all(healthy(campaigns=[camp(status="PAUSED", conversions=0.0, geo_type="PRESENCE_OR_INTEREST")]))
    assert "所見あり" not in statuses(results)
    assert all(r["結果"] == "判定できない" for r in results if r["点検"] != "C09")


def test_点検の中でエラーが出ても判定できないとして残す():
    broken = healthy()
    broken["campaigns"] = [{"status": "ENABLED"}]  # 必要な欄が欠けている
    results = checks.run_all(broken)
    assert {r["点検"] for r in results} == {c.id for c in checks.CHECKS}
    assert any(r["結果"] == "判定できない" and "エラー" in r["理由"] for r in results)


def test_所見には重さと根拠と案と印が付く():
    snap = healthy(campaigns=[camp(conversions=0.0, rank_lost=0.6, budget_lost=0.4, geo_type="PRESENCE_OR_INTEREST")])
    findings = [r for r in checks.run_all(snap) if r["結果"] == "所見あり"]
    assert len(findings) >= 4
    for f in findings:
        assert f["重さ"] in ("大", "中", "小")
        assert f["根拠"] and f["所見"]
        assert f["直し方の案"]
        assert f["誰の意見"] == "AI の提案（未承認）"
        assert f["点検"] and f["版"]


def test_入札と予算の案は必ず最後で_それでも不足ならが付く():
    snap = healthy(campaigns=[camp(conversions=0.0, rank_lost=0.6, budget_lost=0.4)],
                   conversion_setup=setup(biddable=False))
    snap2 = healthy(campaigns=[camp(conversions=0.0, rank_lost=0.6, budget_lost=0.4)])
    seen_bid = 0
    for f in [r for s in (snap, snap2) for r in checks.run_all(s) if r["結果"] == "所見あり"]:
        kinds = [x["種類"] for x in f["直し方の案"]]
        assert [x["順"] for x in f["直し方の案"]] == list(range(1, len(kinds) + 1))
        if checks.BID_KIND in kinds:
            seen_bid += 1
            first_bid = kinds.index(checks.BID_KIND)
            assert first_bid > 0, "入札・予算の案が先頭に来ている"
            assert all(k == checks.BID_KIND for k in kinds[first_bid:])
            for x in f["直し方の案"][first_bid:]:
                assert x["案"].startswith("それでも不足なら") and "承認" in x["案"]
        for x in f["直し方の案"]:
            if x["種類"] != checks.BID_KIND:
                assert "それでも不足なら" not in x["案"]
    assert seen_bid >= 4  # C01 が 2 件、C10 が広告ランクと予算


def test_order_fixesは順番を入れ替えても入札を最後にする():
    out = checks.order_fixes([(checks.BID_KIND, "入札を上げる"), ("品質", "見出しを足す"), ("計測", "タグを確かめる")])
    assert [x["種類"] for x in out] == ["品質", "計測", checks.BID_KIND]
    assert out[-1]["案"] == "それでも不足なら: 入札を上げる（必ず運用者の承認を取る）"


def test_まとめは重い順():
    snap = healthy(campaigns=[camp(conversions=0.0, rank_lost=0.6, geo_type="PRESENCE_OR_INTEREST")], lp_audits=None)
    s = checks.summarize(checks.run_all(snap))
    weights = [f["重さ"] for f in s["所見"]]
    assert weights == sorted(weights, key=checks.WEIGHT_ORDER.get)
    assert s["まとめ"]["所見"] == {"大": 1, "中": 2, "小": 0}
    assert s["まとめ"]["判定できない"] == 1
    assert [r["点検"] for r in s["判定できなかったもの"]] == ["C09"]
    row = next(r for r in s["点検の一覧"] if r["点検"] == "C10")
    assert row["結果"] == "所見あり" and row["いちばん重い所見"] == "大"
    assert next(r for r in s["点検の一覧"] if r["点検"] == "C09")["結果"] == "判定できない"


# ---- C01 入札と成果 ----

def test_C01_成果ベースで成果0件():
    f = one_finding(run("C01", healthy(campaigns=[camp(conversions=0.0, clicks=8)])))
    assert f["重さ"] == "中" and f["根拠"]["期間のクリック数"] == 8
    assert f["根拠"]["期間の費用（円）"] == 30000


def test_C01_クリックが多いのに0件なら大():
    assert one_finding(run("C01", healthy(campaigns=[camp(conversions=0.0, clicks=100)])))["重さ"] == "大"


def test_C01_目標が入札の対象外():
    f = one_finding(run("C01", healthy(conversion_setup=setup(biddable=False))))
    assert f["重さ"] == "大" and f["根拠"]["入札の対象になっている成果地点"] == []


def test_C01_成果地点が止まっていれば対象外と同じ():
    assert one_finding(run("C01", healthy(conversion_setup=setup(action_status="HIDDEN"))))["重さ"] == "大"


def test_C01_キャンペーン専用の目標も見る():
    ok = run("C01", healthy(conversion_setup=setup(custom=True)))
    assert statuses(ok) == ["問題なし"] and "専用の目標" in ok[0]["根拠"]["目標"]
    bad = setup(custom=True)
    bad["actions"][ACTION]["status"] = "HIDDEN"
    assert one_finding(run("C01", healthy(conversion_setup=bad)))["重さ"] == "大"


def test_C01_表示が無ければ判定できない():
    r = run("C01", healthy(campaigns=[camp(impressions=0, clicks=0, conversions=0.0)]))
    assert statuses(r) == ["判定できない"] and "表示が無い" in r[0]["理由"]


def test_C01_成果ベースでない入札は対象外():
    assert statuses(run("C01", healthy(campaigns=[camp(bidding="MANUAL_CPC", conversions=0.0)]))) == ["問題なし"]


# ---- C02 広告文 ----

@pytest.mark.parametrize("over,word", [
    ({"strength": "POOR"}, "低い"),
    ({"headlines": ["a"] * 9}, "見出しが 9 本"),
    ({"descriptions": ["a"] * 3}, "説明文が 3 本"),
])
def test_C02_足りない広告文(over, word):
    f = one_finding(run("C02", healthy(ads=[ad(**over)])))
    assert f["重さ"] == "中" and word in f["所見"]


def test_C02_ちょうど10本と4本は問題なし():
    assert statuses(run("C02", healthy(ads=[ad(headlines=["a"] * 10, descriptions=["b"] * 4, strength="AVERAGE")]))) == ["問題なし"]


def test_C02_不承認と広告なしは大():
    assert one_finding(run("C02", healthy(ads=[ad(approval="DISAPPROVED")])))["重さ"] == "大"
    assert one_finding(run("C02", healthy(ads=[ad(status="PAUSED")])))["重さ"] == "大"


# ---- C03 表示オプション ----

def test_C03_無い種類を挙げる():
    links = [link("SITELINK")] * 6 + [link("CALLOUT")] * 4
    f = one_finding(run("C03", healthy(asset_links=links)))
    assert f["重さ"] == "小"
    assert set(f["根拠"]["無い種類と広告グループ"]) == {"構造化スニペット", "画像"}


def test_C03_サイトリンクが無ければ中():
    f = one_finding(run("C03", healthy(asset_links=[link("CALLOUT")] * 4)))
    assert f["重さ"] == "中" and "サイトリンク" in f["所見"]


def test_C03_階層をまたいで数える():
    links = [link("SITELINK", "account")] * 4 + [link("CALLOUT")] * 4 + [link("STRUCTURED_SNIPPET", "ad_group", G1),
                                                                         link("AD_IMAGE", "ad_group", G1)]
    assert statuses(run("C03", healthy(asset_links=links))) == ["問題なし"]
    # ほかの広告グループに付いているだけでは数えない
    groups = [{"campaign_id": CAMP_ID, "id": G1, "name": "ルール", "status": "ENABLED"},
              {"campaign_id": CAMP_ID, "id": G2, "name": "日程", "status": "ENABLED"}]
    f = one_finding(run("C03", healthy(asset_links=links, ad_groups=groups)))
    assert f["根拠"]["無い種類と広告グループ"] == {"構造化スニペット": ["日程"], "画像": ["日程"]}


def test_C03_数が少ないだけなら小_止まっているものは数えない():
    links = [link("SITELINK")] * 2 + [link("SITELINK", status="PAUSED")] * 4 + [link("CALLOUT")] * 4 \
            + [link("STRUCTURED_SNIPPET"), link("AD_IMAGE")]
    f = one_finding(run("C03", healthy(asset_links=links)))
    assert f["重さ"] == "小" and "サイトリンク 2 件" in f["所見"]


# ---- C04 キーワードと広告のずれ ----

def test_C04_語が1つも出てこないキーワード():
    kws = [kw("ホッケー ルール"), kw("サッカー 観戦"), kw("ソフトボール")]
    f = one_finding(run("C04", healthy(keywords=kws)))
    assert f["根拠"]["該当するキーワード"] == ["サッカー 観戦", "ソフトボール"]
    assert f["重さ"] == "大"  # 3 個のうち 2 個（半分を超える）


def test_C04_一部でも出てくれば挙げない_全角半角と大文字小文字はそろえる():
    kws = [kw("ＨＡＮＤＢＡＬＬ"), kw("ホッケー とは"), kw("example 評判"), kw("ルール"), kw("観戦")]
    f = one_finding(run("C04", healthy(keywords=kws)))
    assert f["根拠"]["該当するキーワード"] == ["観戦"] and f["重さ"] == "中"


def test_C04_広告が無ければ判定できない():
    r = run("C04", healthy(ads=[]))
    assert statuses(r) == ["判定できない"]


# ---- C05 語順だけ違う重複 ----

def test_C05_語順だけ違う部分一致():
    kws = [kw("スポーツ ホッケー"), kw("ホッケー スポーツ"), kw("ホッケー　ルール"), kw("ルール ホッケー", match="PHRASE"),
           kw("ホッケー ルール", status="PAUSED")]
    f = one_finding(run("C05", healthy(keywords=kws)))
    assert f["根拠"]["重複の組の数"] == 1
    assert f["根拠"]["重複の組"] == [["スポーツ ホッケー（ルール）", "ホッケー スポーツ（ルール）"]]


def test_C05_広告グループをまたいでも拾う():
    groups = [{"campaign_id": CAMP_ID, "id": G1, "name": "ルール", "status": "ENABLED"},
              {"campaign_id": CAMP_ID, "id": G2, "name": "SIX", "status": "ENABLED"}]
    kws = [kw("ホッケー"), kw("ホッケー", group=G2)]
    assert one_finding(run("C05", healthy(keywords=kws, ad_groups=groups, ads=[ad(), ad(G2)])))["根拠"]["重複の組の数"] == 1


def test_C05_重複なし():
    assert statuses(run("C05", healthy())) == ["問題なし"]


# ---- C06 クリック 0 の検索語句 ----

def test_C06_クリック0で表示が積もる語句():
    terms = [term("ホッケー ルール", 40, 5), term("ホッケー グローブ", 12), term("live sports", 9),
             term("ホッケー とは", 2), term("ホッケー 用品", 7, status="EXCLUDED")]
    f = one_finding(run("C06", healthy(search_terms=terms)))
    assert f["根拠"]["候補（表示の多い順）"] == [
        {"検索語句": "ホッケー グローブ", "表示回数": 12, "キーワードに登録済み": False},
        {"検索語句": "live sports", "表示回数": 9, "キーワードに登録済み": False}]
    assert f["根拠"]["候補の表示回数"] == 21
    assert f["根拠"]["見えている検索語句の数"] == 5
    assert f["根拠"]["すでに除外語句で止まる見込みなので外した数"] == 1
    assert f["重さ"] == "中"  # 21 ÷ 70 = 30%


def test_C06_もう除外語句で止まる語は候補から外す():
    terms = [term("ホッケー ルール", 80, 5), term("ホッケー グローブ 通販", 12), term("live sports", 9), term("チケット ホッケー", 6)]
    negatives = [{"campaign_id": CAMP_ID, "ad_group_id": None, "text": "グローブ", "match": "PHRASE"},
                 {"campaign_id": CAMP_ID, "ad_group_id": G2, "text": "live", "match": "PHRASE"},   # 別の広告グループ
                 {"campaign_id": CAMP_ID, "ad_group_id": None, "text": "ホッケー チケット", "match": "BROAD"}]
    f = one_finding(run("C06", healthy(search_terms=terms, negatives=negatives)))
    assert [c["検索語句"] for c in f["根拠"]["候補（表示の多い順）"]] == ["live sports"]
    assert f["根拠"]["すでに除外語句で止まる見込みなので外した数"] == 2
    assert f["重さ"] == "小"


def test_除外語句の当たり方():
    assert checks.blocked_by_negative("ホッケー グッズ", "グッズ", "PHRASE")
    assert checks.blocked_by_negative("ホッケーグッズ 通販", "グッズ", "PHRASE")
    assert not checks.blocked_by_negative("ホッケー 用品", "グッズ", "PHRASE")
    assert checks.blocked_by_negative("live sports", "LIVE SPORTS", "EXACT")
    assert not checks.blocked_by_negative("live sports today", "live sports", "EXACT")
    assert checks.blocked_by_negative("チケット 安い ホッケー", "ホッケー チケット", "BROAD")
    assert not checks.blocked_by_negative("ホッケー 日程", "ホッケー チケット", "BROAD")
    assert not checks.blocked_by_negative("sports live", "live sports", "PHRASE")  # フレーズ一致は語順も見る


def test_C06_キーワードに登録済みの語には印を付ける():
    terms = [term("ホッケー ルール", 80, 5), term("ホッケー ルール ブック", 12, status="ADDED")]
    f = one_finding(run("C06", healthy(search_terms=terms)))
    assert f["根拠"]["候補（表示の多い順）"] == [{"検索語句": "ホッケー ルール ブック", "表示回数": 12, "キーワードに登録済み": True}]


def test_C06_検索語句が無ければ判定できない():
    r = run("C06", healthy(search_terms=[]))
    assert statuses(r) == ["判定できない"]


def test_C06_表示が少ない語だけなら問題なし():
    assert statuses(run("C06", healthy(search_terms=[term("a", 2), term("b", 1), term("c", 30, 3)]))) == ["問題なし"]


# ---- C07 検索ボリュームが少ない ----

def test_C07_全部が少ない広告グループ():
    groups = [{"campaign_id": CAMP_ID, "id": G1, "name": "ルール", "status": "ENABLED"},
              {"campaign_id": CAMP_ID, "id": G2, "name": "日程", "status": "ENABLED"}]
    kws = [kw("ホッケー ルール"), kw("ホッケー 初心者", serving="RARELY_SERVED"),
           kw("2028 ホッケー 日程", G2, serving="RARELY_SERVED"), kw("LA2028 ホッケー", G2, serving="RARELY_SERVED"),
           kw("ホッケー 日本代表", G2, status="PAUSED")]
    results = run("C07", healthy(keywords=kws, ad_groups=groups))
    f = one_finding(results)
    assert f["対象"] == "検索A / 日程" and f["重さ"] == "大"
    assert f["根拠"]["検索ボリュームが少ないキーワードの数"] == 2
    assert statuses(results).count("問題なし") == 1


# ---- C08 地域 ----

def test_C08_所在地または関心():
    assert one_finding(run("C08", healthy(campaigns=[camp(geo_type="PRESENCE_OR_INTEREST")])))["重さ"] == "中"
    assert statuses(run("C08", healthy())) == ["問題なし"]
    assert statuses(run("C08", healthy(campaigns=[camp(geo_type="UNSPECIFIED")]))) == ["判定できない"]


# ---- C09 LP の速度 ----

@pytest.mark.parametrize("lcp,fcp,expected", [
    (5.1, 2.8, "大"), (4.1, 1.0, "大"), (4.0, 1.0, "中"), (2.6, 1.0, "中"), (2.0, 3.1, "中"),
    (2.5, 1.9, "小"), (2.5, 1.8, None), (1.2, 0.9, None),
])
def test_C09_速度の線引き(lcp, fcp, expected):
    r = run("C09", healthy(lp_audits=[lp_audit(lcp, fcp)]))
    if expected is None:
        assert statuses(r) == ["問題なし"]
    else:
        f = one_finding(r)
        assert f["重さ"] == expected and f["根拠"]["メイン画像の表示（秒）"] == lcp
        assert f["根拠"]["測り方"] == "手元の Lighthouse"


def test_C09_測れなければ判定できない():
    r = run("C09", healthy(lp_audits=[lp_audit(ok=False, reason="PageSpeed Insights が HTTP 429 を返した")]))
    assert statuses(r) == ["判定できない"] and "429" in r[0]["理由"]
    assert statuses(run("C09", healthy(lp_audits=None))) == ["判定できない"]
    assert statuses(run("C09", healthy(lp_audits=[]))) == ["判定できない"]


# ---- C10 失った表示の割合 ----

@pytest.mark.parametrize("rank,budget,expected", [
    (0.568, 0.25, {"広告ランク": "大", "予算": "小"}),
    (0.30, 0.30, {"広告ランク": "中", "予算": "中"}),
    (0.10, 0.0999, {"広告ランク": "小"}),
    (0.0999, 0.0999, {}),
])
def test_C10_失った割合(rank, budget, expected):
    r = run("C10", healthy(campaigns=[camp(rank_lost=rank, budget_lost=budget)]))
    got = {("広告ランク" if "広告ランク" in f["対象"] else "予算"): f["重さ"] for f in r if f["結果"] == "所見あり"}
    assert got == expected
    if not expected:
        assert statuses(r) == ["問題なし"]


def test_C10_数字は百分率で返す():
    f = next(x for x in run("C10", healthy(campaigns=[camp(rank_lost=0.5683391, budget_lost=0.25, search_is=0.1816609)]))
             if "広告ランク" in x["対象"])
    assert f["根拠"]["広告ランクで失った割合（%）"] == 56.8
    assert f["根拠"]["予算で失った割合（%）"] == 25.0
    assert f["根拠"]["表示できた割合（%）"] == 18.2
    assert f["根拠"]["1 日の予算（円）"] == 1500


def test_C10_表示が無ければ判定できない():
    r = run("C10", healthy(campaigns=[camp(impressions=0, rank_lost=None, budget_lost=None, search_is=None)]))
    assert statuses(r) == ["判定できない"]


def test_点検はデータを書き換えない():
    snap = healthy(campaigns=[camp(conversions=0.0, rank_lost=0.6)])
    before = copy.deepcopy(snap)
    checks.run_all(snap)
    assert snap == before
