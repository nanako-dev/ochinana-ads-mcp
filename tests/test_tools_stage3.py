"""段 3 の道具の返答の形を、にせの API の行で確かめる。本物の API にも外にもつながない。"""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import config, forecast, gaql, intel, tools

TODAY = date(2026, 11, 11)


def E(name):
    return NS(name=name)


CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))


def kw_row(text, imp, clicks, yen, share, channel="SEARCH"):
    return NS(campaign=NS(id=1, advertising_channel_type=E(channel)), ad_group=NS(id=10),
              ad_group_criterion=NS(keyword=NS(text=text, match_type=E("BROAD"))),
              metrics=NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, search_impression_share=share))


def campaign_rows(imp=216, clicks=8, yen=9919, share=0.1816609, ai_max=True):
    return [NS(
        campaign=NS(id=1, name="検索A", status=E("ENABLED"), advertising_channel_type=E("SEARCH"),
                    bidding_strategy_type=E("MAXIMIZE_CONVERSIONS"),
                    geo_target_type_setting=NS(positive_geo_target_type=E("PRESENCE")),
                    ai_max_setting=NS(enable_ai_max=ai_max)),
        campaign_budget=NS(amount_micros=1_500_000_000),
        metrics=NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, conversions=0.0,
                   search_impression_share=share, search_rank_lost_impression_share=0.57,
                   search_budget_lost_impression_share=0.25),
    )]


def api_rows(**over):
    rows = {
        "customer": [CUSTOMER],
        "campaign": campaign_rows(),
        "campaign_criterion": [
            NS(campaign=NS(id=1), campaign_criterion=NS(type_=E("LOCATION"), location=NS(geo_target_constant="geoTargetConstants/2392"),
                                                        language=NS(language_constant=""))),
            NS(campaign=NS(id=1), campaign_criterion=NS(type_=E("LANGUAGE"), location=NS(geo_target_constant=""),
                                                        language=NS(language_constant="languageConstants/1005"))),
        ],
        "geo_target_constant": [NS(geo_target_constant=NS(id=2392, name="Japan", country_code="JP"))],
        "language_constant": [NS(language_constant=NS(id=1005, name="Japanese", code="ja"))],
        "keyword_view": [kw_row("ホッケー ルール", 46, 3, 2469, 0.15), kw_row("ホッケー", 87, 2, 2029, 0.1777),
                         kw_row("ディスプレイの語", 9999, 99, 9999, 0.9, channel="DISPLAY")],
    }
    rows.update(over)
    return rows


def fake_run(rows, seen=None):
    def run(customer_id, query):
        assert query.startswith("SELECT ")
        if seen is not None:
            seen.append(query)
        return rows[query.split(" FROM ")[1].split()[0]]
    return run


def planner_ok(calls=None):
    def planner(cid, keywords, geo_id, language_id):
        if calls is not None:
            calls.append((cid, list(keywords), geo_id, language_id))
        return [{"text": k, "close_variants": [], "avg_monthly_searches": 1000, "monthly": [1000] * 12,
                 "low_bid_micros": 50_000_000, "high_bid_micros": 300_000_000} for k in keywords]
    return planner


class Denied(Exception):
    """Google 広告 API が権限で断ったときの形。"""

    def __init__(self):
        super().__init__("denied")
        self.failure = NS(errors=[NS(error_code=NS(WhichOneof=lambda _: "authorization_error"),
                                     message="This method is not allowed for use with explorer access. Please apply for basic or standard access.")])
        self.request_id = "req-1"


def planner_denied(*args):
    raise Denied()


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path / "out"))
    monkeypatch.setenv(forecast.ENV_LOG_DIR, str(tmp_path / "log"))
    yield
    tools._accounts.clear()


def test_配信量の見込み(tmp_path):
    calls, seen = [], []
    out = tools.forecast_volume("111-222-3333", ["ホッケー ルール", "ホッケー とは"], run=fake_run(api_rows(), seen),
                                planner=planner_ok(calls), today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert "2026-05-15〜2026-11-10" in out["期間"]  # 昨日までの 180 日
    assert calls == [("1112223333", ["ホッケー ルール", "ホッケー とは"], 2392, 1005)]  # 地域と言語はアカウントの既定値
    assert out["地域と言語"]["言語"]["言語コード"] == "ja"
    row = out["行"][0]
    assert row["月間検索数"] == 1000 and row["検索数の出どころ"] == "キーワードプランナー（Google 広告 API）"
    assert row["ページ上部に出すための入札の目安（円）"] == [50, 300]
    used = row["使った割合・率・単価"]
    assert used["表示される割合（%）"]["値"] == 18.17 and used["表示される割合（%）"]["出どころ"] == "アカウント全体の実績で代用"
    assert used["クリック率（%）"]["値"] == 3.7 and used["クリック単価（円）"]["値"] == 1240
    assert used["このキーワードの実績"] == {"表示回数": 46, "クリック数": 3}
    # 中: 1,000 × 18.17% = 182 回 → × 3.70% = 6.7 クリック → × 1,240 円 = 8,342 円
    assert row["見込み（月）"]["表示回数"]["中"] == 182
    assert row["見込み（月）"]["クリック数"]["中"] == 6.7
    assert row["見込み（月）"]["費用（円）"]["中"] == pytest.approx(8342, abs=3)
    assert row["見込み（月）"]["費用（円）"]["低"] < row["見込み（月）"]["費用（円）"]["中"] < row["見込み（月）"]["費用（円）"]["高"]
    assert any("アカウント全体の値で代用" in n for n in row["注記"])
    assert out["合計の見込み（月）"]["表示回数"]["中"] == 363  # 181.66 × 2（丸める前の値を足す）
    assert any("アカウント全体の実績も少ない（表示 216・クリック 8）" in n for n in out["注記"])
    assert "検索数 × 表示される割合" in out["式"].replace("月間", "")
    assert out["これは何か"].startswith("道具が計算した見込み")
    # 記録される
    log = (tmp_path / "log" / "forecasts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and json.loads(log[0])["id"] == out["記録"]["id"]
    assert "micros" not in json.dumps(out, ensure_ascii=False)
    assert all(q.startswith("SELECT ") for q in seen)


def test_キーワードプランナーが断られたら理由と次の手を返す(tmp_path):
    out = tools.forecast_volume("1112223333", ["ホッケー ルール"], run=fake_run(api_rows()), planner=planner_denied, today=TODAY)
    row = out["行"][0]
    assert row["月間検索数"] is None and row["見込み（月）"] is None and row["検索数の出どころ"] == "取れなかった"
    assert "explorer access" in row["見込みを出せない理由"] and "monthly_searches" in row["見込みを出せない理由"]
    assert row["使った割合・率・単価"]["クリック単価（円）"]["値"] == 1240  # 自分の実績の側は返す
    assert out["合計の見込み（月）"] is None
    assert out["記録"] == "見込みが 1 つも出なかったので、記録していない"
    assert not (tmp_path / "log" / "forecasts.jsonl").exists()


def test_検索数を渡せばプランナーを呼ばずに計算する():
    def planner(*args):
        raise AssertionError("呼んではいけない")

    out = tools.forecast_volume("1112223333", ["ホッケー"], daily_budget_yen=100, monthly_searches={"ホッケー": 49500},
                                run=fake_run(api_rows()), planner=planner, today=TODAY, record=False)
    row = out["行"][0]
    assert row["月間検索数"] == 49500 and row["検索数の出どころ"] == "渡された値"
    # 予算: 100 円 × 30.4 日 = 3,040 円で頭打ち
    assert row["見込み（月）"]["費用（円）"]["中"] == 3040 and row["見込み（月）"]["予算で頭打ち"] is True
    assert out["合計の見込み（月）"]["費用（円）"]["高"] == 3040
    for metric in ("表示回数", "クリック数", "費用（円）"):
        v = row["見込み（月）"][metric]
        assert v["低"] <= v["中"] <= v["高"], (metric, v)
    assert any("頭打ち" in n for n in out["注記"])
    assert "予算で頭打ち" not in out["合計の見込み（月）"]
    assert "記録" not in out


def test_日本語のキーワードなのに言語が英語なら注記する():
    rows = api_rows(language_constant=[NS(language_constant=NS(id=1000, name="English", code="en"))])
    out = tools.forecast_volume("1112223333", ["ホッケー ルール"], run=fake_run(rows), planner=planner_ok(), today=TODAY, record=False)
    assert any("日本語のキーワード" in n and "en" in n for n in out["注記"])


def test_実績が無いアカウントでは見込みを出さない():
    rows = api_rows(campaign=campaign_rows(0, 0, 0, 0.0), keyword_view=[])
    out = tools.forecast_volume("1112223333", ["ホッケー"], run=fake_run(rows), planner=planner_ok(), today=TODAY, record=False)
    assert out["行"][0]["見込み（月）"] is None
    assert "実績が無い" in out["行"][0]["見込みを出せない理由"]


def test_見込みの入力を確かめる():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    with pytest.raises(ValueError, match="許可リスト"):
        tools.forecast_volume("1234567890", ["a"], run=run)
    with pytest.raises(ValueError, match="1 個以上"):
        tools.forecast_volume("1112223333", [" ", ""], run=run)
    with pytest.raises(ValueError, match="50 個まで"):
        tools.forecast_volume("1112223333", [f"k{i}" for i in range(51)], run=run)
    with pytest.raises(ValueError, match="予算"):
        tools.forecast_volume("1112223333", ["a"], daily_budget_yen=0, run=run)
    assert tools._keyword_list("ホッケー ルール、ホッケー とは,ホッケー　ルール") == ["ホッケー ルール", "ホッケー とは"]


def test_見込みと実績を比べる():
    run = fake_run(api_rows())
    made = tools.forecast_volume("1112223333", ["ホッケー ルール", "ホッケー とは"], run=run, planner=planner_ok(), today=TODAY)
    listing = tools.forecast_vs_actual("1112223333", run=run)
    assert listing["件数"] == 1 and listing["行"][0]["id"] == made["記録"]["id"]
    assert listing["行"][0]["キーワード"] == ["ホッケー ルール", "ホッケー とは"]
    out = tools.forecast_vs_actual("1112223333", "2026-11-12", "2026-12-11", run=run)   # 30 日
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"] and "30 日" in out["期間"]
    assert out["使った見込み"]["id"] == made["記録"]["id"]
    row = out["行"][0]
    assert row["キーワード"] == "ホッケー ルール" and row["表示回数"]["実績"] == 46
    assert row["表示回数"]["見込み（期間に換算）"]["中"] == round(182 * 30 / 30.4)
    assert row["費用（円）"]["実績"] == 2469
    assert out["行"][1]["実績"] is None
    with pytest.raises(ValueError, match="見つからない"):
        tools.forecast_vs_actual("1112223333", "2026-11-12", "2026-12-11", forecast_id="F-none", run=run)
    with pytest.raises(ValueError, match="まだ無い"):
        tools.forecast_vs_actual("4445556666", "2026-11-12", "2026-12-11", run=run)


def test_カードをアカウントに照らす(tmp_path, monkeypatch):
    (tmp_path / "intel" / "cards").mkdir(parents=True)
    monkeypatch.setenv(intel.ENV_INTEL_DIR, str(tmp_path / "intel"))
    (tmp_path / "intel" / "cards" / "c1.md").write_text(
        '---\nid: 2026-11-10-lang\n何が変わったか: "検索キャンペーンで手動の言語指定が廃止される。"\n出どころ:\n  url: "https://example.com/x"\n'
        '  段: "A"\n  日付: "2026-09-30"\n関係する設定: ["検索キャンペーン", "AI Max", "CampaignCriterion.language", "ジオフェンス"]\n'
        '信頼度: 公式\n扱い: 未決\n期限: "2026-09-30"\n入った日: 2026-11-10\n下書き: "AI（codex）の下書き。人の確認はまだ"\n---\n\n# Language targeting\n',
        encoding="utf-8")
    seen = []
    out = tools.match_card_to_account("lang", "1112223333", run=fake_run(api_rows(), seen), today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["判定"] == "当てはまる"
    assert {r["見たもの"] for r in out["照らした点"]} == {"キャンペーンの種類: 検索キャンペーン", "AI Max を有効にしているキャンペーン",
                                                    "キャンペーンの言語の指定"}
    assert out["機械で照らせなかった語"] == ["ジオフェンス"]
    assert any("AI の下書き" in n for n in out["必ず伝えること"])
    assert out["カード"]["期限まで（日）"] == -42
    state = out["アカウントの状態（道具で読める範囲）"]
    assert state["直近 30 日の成果の件数"] == 0.0 and state["この道具"]["API の版"].startswith("v")
    assert all(q.startswith("SELECT ") for q in seen)
    with pytest.raises(ValueError, match="許可リスト"):
        tools.match_card_to_account("lang", "1234567890", run=fake_run(api_rows()))
    with pytest.raises(ValueError, match="見つからない"):
        tools.match_card_to_account("nothing", "1112223333", run=fake_run(api_rows()))


def test_入札の目安が返らない語は値なしにする():
    def planner(cid, keywords, geo_id, language_id):
        return [{"text": k, "close_variants": [], "avg_monthly_searches": 2900, "monthly": [2900] * 12,
                 "low_bid_micros": 0, "high_bid_micros": 0} for k in keywords]

    out = tools.forecast_volume("1112223333", ["ホッケー ルール"], run=fake_run(api_rows()), planner=planner, today=TODAY, record=False)
    row = out["行"][0]
    assert row["ページ上部に出すための入札の目安（円）"] is None  # [0, 0] と出すと「0 円」に見える
    assert any("値を返さなかった" in n for n in row["注記"])
    assert row["月間検索数"] == 2900 and row["見込み（月）"] is not None


def test_入札の目安は100万分の1円を円に直す():
    """2026-11-21 にキーワード プランナーの画面と突き合わせた値（ホッケー 東京: 画面は 2 円と 5 円）。"""
    def planner(cid, keywords, geo_id, language_id):
        return [{"text": k, "close_variants": [], "avg_monthly_searches": 33100, "monthly": [33100] * 12,
                 "low_bid_micros": 1_745_491, "high_bid_micros": 4_808_937} for k in keywords]

    out = tools.forecast_volume("1112223333", ["ホッケー 東京"], run=fake_run(api_rows()), planner=planner, today=TODAY, record=False)
    assert out["行"][0]["ページ上部に出すための入札の目安（円）"] == [2, 5]


def test_段3の問い合わせ文():
    q = gaql.keyword_share_query("2026-05-01", "2026-11-10")
    assert q.startswith("SELECT ") and "search_impression_share" in q and "negative = FALSE" in q
    assert "campaign.ai_max_setting.enable_ai_max" in gaql.campaign_state_query()
    with pytest.raises(ValueError):
        gaql.keyword_share_query("2026-11-10", "2026-05-01")
