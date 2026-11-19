"""配信量の見込みの計算と、見込みと実績の照合。API は呼ばない。"""
from datetime import datetime, timezone

import pytest

from ochinana_ads import forecast


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    monkeypatch.setenv(forecast.ENV_LOG_DIR, str(tmp_path))
    return tmp_path


def stat(imp, clicks, yen, share=None):
    return {"impressions": imp, "clicks": clicks, "cost_micros": yen * 1_000_000, "search_is": share}


def test_割合の幅は件数が少ないほど広い():
    low, high = forecast.wilson(8, 216)
    # 手計算: p = 0.037037、z = 1.96 → 中心 0.04526、半幅 0.02627 → 0.0190〜0.0715
    assert low == pytest.approx(0.0190, abs=0.0005) and high == pytest.approx(0.0715, abs=0.0005)
    wide = forecast.wilson(1, 20)
    narrow = forecast.wilson(100, 2000)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
    assert forecast.wilson(0, 0) == (0.0, 1.0)
    assert forecast.wilson(0, 50)[0] == 0.0 and forecast.wilson(50, 50)[1] == pytest.approx(1.0)


def test_表示される割合は表示できたはずの回数で重みを付ける():
    # 100 回表示・割合 50% → 資格 200。300 回表示・割合 25% → 資格 1200。合わせて 400 ÷ 1400 = 28.6%
    c = forecast.combine([stat(100, 5, 500, 0.5), stat(300, 9, 900, 0.25)])
    assert c["impressions"] == 400 and c["clicks"] == 14 and c["cost_micros"] == 1_400_000_000
    assert c["eligible"] == pytest.approx(1400)
    rates = forecast.pick_rates(None, c)
    assert rates["share"]["mid"] == pytest.approx(400 / 1400)


def test_実績が足りるキーワードは自分の値を使う():
    own = forecast.combine([stat(1000, 50, 20000, 0.4)])
    account = forecast.combine([stat(5000, 100, 100000, 0.2)])
    r = forecast.pick_rates(own, account)
    assert r["share"]["source"] == r["ctr"]["source"] == r["cpc"]["source"] == forecast.SOURCE_OWN
    assert r["share"]["mid"] == pytest.approx(0.4)
    assert r["ctr"]["mid"] == pytest.approx(0.05)
    assert r["cpc"]["mid"] == pytest.approx(400)
    assert (r["cpc"]["low"], r["cpc"]["high"]) == pytest.approx((280, 520))  # クリック 50 件 → ±30%


def test_実績が少ないキーワードはアカウント全体で代用する():
    own = forecast.combine([stat(46, 3, 2469, 0.15)])     # 表示 100 未満、クリック 10 未満
    account = forecast.combine([stat(216, 8, 9919, 0.1816609)])
    r = forecast.pick_rates(own, account)
    assert r["share"]["source"] == r["ctr"]["source"] == r["cpc"]["source"] == forecast.SOURCE_ACCOUNT
    assert r["share"]["mid"] == pytest.approx(0.1816609, rel=1e-6)
    assert r["ctr"]["mid"] == pytest.approx(8 / 216)
    assert r["cpc"]["mid"] == pytest.approx(9919 / 8)
    assert (r["cpc"]["low"], r["cpc"]["high"]) == pytest.approx((9919 / 8 * 0.5, 9919 / 8 * 1.5))  # クリック 8 件 → ±50%
    assert r["own"] == {"impressions": 46, "clicks": 3}


def test_表示は足りるがクリックが足りないときは単価だけ代用():
    own = forecast.combine([stat(500, 4, 2000, 0.3)])
    account = forecast.combine([stat(5000, 100, 100000, 0.2)])
    r = forecast.pick_rates(own, account)
    assert r["share"]["source"] == r["ctr"]["source"] == forecast.SOURCE_OWN
    assert r["cpc"]["source"] == forecast.SOURCE_ACCOUNT and r["cpc"]["mid"] == pytest.approx(1000)


def test_アカウントにも実績が無ければ率は決められない():
    r = forecast.pick_rates(None, forecast.combine([]))
    assert r["share"] is None and r["ctr"] is None and r["cpc"] is None
    assert forecast.project(forecast.volume_range(1000, None), r) is None


def test_検索数の幅は年平均と直近3か月():
    v = forecast.volume_range(1900, [110, 150, 200, 260, 2900, 5400, 4000])
    assert v["mid"] == 1900 and v["recent3"] == 4100 and (v["low"], v["high"]) == (1900, 4100)
    v = forecast.volume_range(1000, None)
    assert (v["low"], v["mid"], v["high"]) == (1000, 1000, 1000) and v["recent3"] is None
    assert forecast.volume_range(None, None) is None


def test_見込みは式どおり():
    rates = {"share": {"low": 0.1, "mid": 0.2, "high": 0.3}, "ctr": {"low": 0.02, "mid": 0.05, "high": 0.08},
             "cpc": {"low": 50, "mid": 100, "high": 150}}
    p = forecast.project({"low": 10000, "mid": 10000, "high": 10000}, rates)
    # 中: 10,000 × 20% = 2,000 回 → × 5% = 100 クリック → × 100 円 = 10,000 円
    assert p["表示回数"] == {"低": 1000, "中": 2000, "高": 3000}
    assert p["クリック数"] == {"低": 20.0, "中": 100.0, "高": 240.0}
    assert p["費用（円）"] == {"低": 1000, "中": 10000, "高": 36000}
    assert p["予算で頭打ち"] is False


def test_予算で頭打ちにする():
    rates = {"share": {"low": 0.2, "mid": 0.2, "high": 0.2}, "ctr": {"low": 0.05, "mid": 0.05, "high": 0.05},
             "cpc": {"low": 100, "mid": 100, "high": 100}}
    p = forecast.project({"low": 10000, "mid": 10000, "high": 10000}, rates, monthly_budget_yen=5000)
    assert p["費用（円）"]["中"] == 5000 and p["クリック数"]["中"] == 50.0 and p["表示回数"]["中"] == 1000
    assert p["予算で頭打ち"] is True


def test_頭打ちのときも低中高の順は崩れない():
    """予算が効くと、単価が安いほど多く買える。低は単価が高い場合、高は単価が安い場合で決まる。"""
    rates = {"share": {"low": 0.16, "mid": 0.18, "high": 0.20}, "ctr": {"low": 0.019, "mid": 0.037, "high": 0.071},
             "cpc": {"low": 620, "mid": 1240, "high": 1860}}
    p = forecast.project({"low": 49500, "mid": 49500, "high": 49500}, rates, monthly_budget_yen=45600)
    for metric in ("表示回数", "クリック数", "費用（円）"):
        assert p[metric]["低"] <= p[metric]["中"] <= p[metric]["高"], (metric, p[metric])
    # クリック: 低 = 45,600 ÷ 1,860 = 24.5、中 = 45,600 ÷ 1,240 = 36.8、高 = 45,600 ÷ 620 = 73.5
    assert p["クリック数"] == {"低": 24.5, "中": 36.8, "高": 73.5}
    # 表示: 低 = 45,600 ÷ (1,860 × 7.1%) = 345、中 = 36.8 ÷ 3.7% = 994、高 = 45,600 ÷ (620 × 1.9%) = 3,871
    assert p["表示回数"]["低"] == 345 and p["表示回数"]["中"] == 994 and p["表示回数"]["高"] == 3871
    assert p["費用（円）"] == {"低": 45600, "中": 45600, "高": 45600}
    assert p["予算で頭打ち"] is True


def test_予算が見込みより大きければ何も変わらない():
    rates = {"share": {"low": 0.1, "mid": 0.2, "high": 0.3}, "ctr": {"low": 0.02, "mid": 0.05, "high": 0.08},
             "cpc": {"low": 50, "mid": 100, "high": 150}}
    vol = {"low": 10000, "mid": 10000, "high": 10000}
    free = forecast.project(vol, rates)
    assert forecast.project(vol, rates, monthly_budget_yen=1_000_000) == free
    # 高の見込みだけが予算を超える場合: 中までは同じで、高の費用だけ予算に止まる
    part = forecast.project(vol, rates, monthly_budget_yen=20000)
    assert part["費用（円）"] == {"低": 1000, "中": 10000, "高": 20000} and part["予算で頭打ち"] is False
    assert part["クリック数"]["低"] == 20.0 and part["クリック数"]["中"] == 100.0 and part["クリック数"]["高"] == 240.0


def test_合計と合計の頭打ち():
    rates = {"share": {"low": 0.2, "mid": 0.2, "high": 0.2}, "ctr": {"low": 0.05, "mid": 0.05, "high": 0.05},
             "cpc": {"low": 100, "mid": 100, "high": 100}}
    raw = forecast.raw_projection({"low": 10000, "mid": 10000, "high": 10000}, rates)   # 1 個で 100 クリック・10,000 円
    total = forecast.add_up([raw, None, raw])
    assert total["費用（円）"] == {"低": 20000, "中": 20000, "高": 20000} and total["クリック数"]["中"] == 200.0
    assert total["予算で頭打ち"] is False
    capped = forecast.add_up([raw, raw], 15000)
    assert capped["費用（円）"]["中"] == 15000 and capped["クリック数"]["中"] == 150.0 and capped["表示回数"]["中"] == 3000
    assert capped["予算で頭打ち"] is True
    assert forecast.add_up([None]) is None
    view = {"表示回数": {"低": 10, "中": 20, "高": 30}, "クリック数": {"低": 1.0, "中": 2.0, "高": 3.0},
            "費用（円）": {"低": 100, "中": 200, "高": 300}}
    assert forecast.sum_views([view, None, view])["費用（円）"] == {"低": 200, "中": 400, "高": 600}
    assert forecast.sum_views([None]) is None


def test_キーワードの文字と言語の食い違い():
    assert forecast.script_notes(["ホッケー ルール"], "ja") == []
    notes = forecast.script_notes(["ホッケー ルール", "handball"], "en")
    assert len(notes) == 1 and "日本語のキーワード" in notes[0] and "en" in notes[0]
    assert "英数字だけ" in forecast.script_notes(["handball teams", "ホッケー"], "ja")[0]
    assert "未定" in forecast.script_notes(["ホッケー"], None)[0]


def test_キーワードは全角半角と大小をそろえて照らす():
    assert forecast.normalize("ＨＡＮＤＢＡＬＬ　ルール ") == forecast.normalize("handball ルール")


PROJ = {"表示回数": {"低": 304, "中": 608, "高": 912}, "クリック数": {"低": 15.2, "中": 30.4, "高": 60.8},
        "費用（円）": {"低": 15200, "中": 30400, "高": 60800}}


def test_見込みを記録して読む(_log):
    rows = [{"キーワード": "ホッケー ルール", "月間検索数": 1000, "見込み（月）": PROJ},
            {"キーワード": "検索数なし", "月間検索数": None, "見込み（月）": None}]
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    r = forecast.record("1112223333", rows, PROJ, "2026-05-15〜2026-11-10", 1500, now=now)
    assert r["id"].startswith("F-20261111-120000-")
    forecast.record("4445556666", rows, PROJ, "x", None)
    mine = forecast.read_records("1112223333")
    assert len(mine) == 1 and len(forecast.read_records()) == 2
    assert [k["キーワード"] for k in mine[0]["キーワード"]] == ["ホッケー ルール"]  # 見込みが出たものだけ残す
    assert mine[0]["1 日の予算（円）"] == 1500 and mine[0]["合計の見込み（月）"] == PROJ
    assert "ホッケー" in (_log / "forecasts.jsonl").read_text(encoding="utf-8")


def test_見込みと実績を並べる():
    entry = {"キーワード": [{"キーワード": "ホッケー ルール", "月間検索数": 1000, "見込み（月）": PROJ},
                       {"キーワード": "ホッケー とは", "月間検索数": 500, "見込み（月）": PROJ}]}
    actual = {forecast.normalize("ホッケー　ルール"): {"impressions": 150, "clicks": 9, "cost_micros": 7_000_000_000}}
    out = forecast.compare(entry, actual, "2026-11-01", "2026-11-10")   # 10 日 → 月の見込み × 10/30.4
    assert out["日数"] == 10
    row = out["行"][0]
    assert row["表示回数"]["見込み（期間に換算）"] == {"低": 100, "中": 200, "高": 300}
    assert row["表示回数"]["実績"] == 150 and row["表示回数"]["差（実績 − 見込みの中）"] == -50
    assert row["表示回数"]["実績 ÷ 見込みの中"] == 0.75 and row["表示回数"]["判定"] == "見込みの幅の中"
    assert row["クリック数"]["見込み（期間に換算）"] == {"低": 5.0, "中": 10.0, "高": 20.0}
    assert row["費用（円）"]["実績"] == 7000 and row["費用（円）"]["判定"] == "見込みの幅の中"
    assert out["行"][1]["実績"] is None and "実績が無い" in out["行"][1]["理由"]
    assert out["合計"]["表示回数"]["実績"] == 150  # 実績が無いキーワードは合計に入れない
    assert out["合計"]["表示回数"]["見込み（期間に換算）"]["中"] == 200


def test_幅から外れたら外れたと返す():
    entry = {"キーワード": [{"キーワード": "a", "月間検索数": 1, "見込み（月）": PROJ}]}
    few = forecast.compare(entry, {"a": {"impressions": 10, "clicks": 0, "cost_micros": 0}}, "2026-11-01", "2026-11-10")
    assert few["行"][0]["表示回数"]["判定"] == "見込みの幅より少ない"
    many = forecast.compare(entry, {"a": {"impressions": 999, "clicks": 50, "cost_micros": 0}}, "2026-11-01", "2026-11-10")
    assert many["行"][0]["表示回数"]["判定"] == "見込みの幅より多い"
    assert many["行"][0]["クリック数"]["判定"] == "見込みの幅より多い"
