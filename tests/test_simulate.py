"""CPA と ROAS の試算（cpa_roas_simulate）。手で計算した値と照らす。外にも API にもつながない。"""
import json
import math
from pathlib import Path

import pytest

from ochinana_ads import kb, simulate


@pytest.fixture(autouse=True)
def _no_kb(monkeypatch, tmp_path):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(tmp_path / "no-kb"))


BASE = dict(monthly_budget_yen=300000, cpc_yen=120, cvr=0.02, value_per_conversion_yen=15000, gross_margin_rate=0.4)


def plan(out, name):
    return next(r for r in out["予算の案"]["案"] if r["案"] == name)


def test_順方向_手計算と同じ():
    out = simulate.cpa_roas_simulate(**BASE)
    f = out["試算（この予算のとき）"]
    # 300,000 ÷ 120 ＝ 2,500 クリック、× 2% ＝ 50 件、CPA 6,000、売上 750,000、ROAS 2.5、粗利 300,000、利益 0
    assert (f["クリック数"], f["成果の件数"], f["平均の CPA"], f["売上"], f["ROAS"], f["粗利"], f["広告費を引いた利益"]) == \
        (2500, 50, 6000, 750000, 2.5, 300000, 0)
    assert f["最後に足した分の CPA"] == 6000  # 頭打ちなしなら平均と同じ
    assert out["採算の線"]["損益分岐の CPA"] == 6000 and out["採算の線"]["損益分岐の ROAS"]["倍"] == 2.5  # 15,000 × 0.4、1 ÷ 0.4
    assert any("平均の CPA と最後に足した分の CPA は同じ" in n for n in out["読み方の注意"])
    assert any("30.4" in n for n in out["読み方の注意"]) and "CPA" in out["式"]
    assert [r["成果の件数"] for r in out["予算の案"]["案"]] == [50, 75, 100]  # 比例


def test_逆算():
    out = simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02, value_per_conversion_yen=15000, target_roas=3, target_conversions=80)
    r = out["逆算"]
    assert r["目標の ROAS を CPA に直すと"]["CPA"] == 5000  # 15,000 ÷ 3
    g = r["目標の CPA に届くには"]
    assert g["いまのクリック単価のままなら要るコンバージョン率"] == 0.024  # 120 ÷ 5,000
    assert g["いまのコンバージョン率のままなら出せるクリック単価の上限"] == 100  # 5,000 × 2%
    assert r["目標の件数に要る予算"]["要る予算"] == 480000  # 80 × 6,000
    out = simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02, target_cpa_yen=100)
    assert "100%" in str(out["逆算"]["目標の CPA に届くには"]["いまのクリック単価のままなら要るコンバージョン率"])
    out = simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02, value_per_conversion_yen=15000, target_roas=3, target_cpa_yen=4000)
    assert out["逆算"]["目標の CPA に届くには"]["いまのコンバージョン率のままなら出せるクリック単価の上限"] == 80  # 厳しい方（4,000）


def test_感度の表_率と単価は同じ効き方():
    out = simulate.cpa_roas_simulate(**BASE)
    t = out["感度の表"]["表"]
    mid = next(row for row in t if row["クリック単価の倍率"] == 1.0)
    low_cpc = next(row for row in t if row["クリック単価の倍率"] == 0.8)
    assert mid["コンバージョン率 ×1.2（2.40%）"]["CPA"] == 5000  # 120 ÷ 0.024
    assert low_cpc["コンバージョン率 ×1（2.00%）"]["CPA"] == 4800  # 96 ÷ 0.02
    assert mid["コンバージョン率 ×1.2（2.40%）"]["ROAS"] == 3.0
    assert "コンバージョン率 ×1.2 ＝ クリック単価 ×0.833（CPA ×0.833）" in out["感度の表"]["同じ効き方の例"]


def test_検索の上限で頭打ち():
    out = simulate.cpa_roas_simulate(**{**BASE, "max_monthly_clicks": 3500})
    # 上限 3,500 クリック × 2% ＝ 70 件。上限に届く予算 3,500 × 120 ＝ 420,000
    assert out["予算の案"]["頭打ち"] == {"検索の上限に届く予算": 420000, "そのときの件数": 70.0}
    p15, p2 = plan(out, "予算 ×1.5"), plan(out, "予算 ×2")
    assert p15["成果の件数"] == 70 and p15["前の案から増やした分の CPA"] == 7500  # 150,000 ÷ 20 件
    assert p2["前の案から増やした分の CPA"] == "増えない（頭打ち）" and p2["最後に足した分の CPA"].startswith("頭打ち")
    assert out["逆算"] == "目標（target_cpa_yen・target_roas・target_conversions）を渡すと出す"
    out = simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02, max_monthly_clicks=3500, target_conversions=80)
    assert out["逆算"]["目標の件数に要る予算"]["要る予算"].startswith("届かない")


def test_出すほど効きが鈍る_いまの実績に合わせる():
    out = simulate.cpa_roas_simulate(**{**BASE, "saturation_spend_yen": 400000})
    p1, p15, p2 = plan(out, "予算 ×1"), plan(out, "予算 ×1.5"), plan(out, "予算 ×2")
    assert p1["成果の件数"] == 50 and p1["平均の CPA"] == 6000  # いまの予算では渡した値どおり
    # 手計算: x ＝ 0.75、r ＝ (0.02/120) × 0.75 ÷ (1 − e^−0.75)、件数(450,000) ＝ r × 400,000 × (1 − e^−1.125)
    r = (0.02 / 120) * 0.75 / (1 - math.exp(-0.75))
    assert p15["成果の件数"] == round(r * 400000 * (1 - math.exp(-1.125)), 2)
    assert p1["最後に足した分の CPA"] == round(math.exp(0.75) / r)
    assert p1["最後に足した分の CPA"] < p15["最後に足した分の CPA"] < p2["最後に足した分の CPA"]  # 増やすほど高い
    assert p15["前の案から増やした分の CPA"] > p1["平均の CPA"]
    assert any("いまの予算で渡されたクリック単価" in a for a in out["前提"]) and any("仮置き" in a for a in out["前提"])
    assert "66.7" not in str(out["予算の案"]["頭打ち"])  # 合わせた後の上限は r × S
    top = r * 400000
    assert f"{top:.1f} 件" in out["予算の案"]["頭打ち"]
    assert "元のコードは写していない" in out["考え方の出どころ"]


def test_入力の誤り():
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(cpc_yen=120, cvr=2, monthly_budget_yen=1000)  # 率は 0〜1
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02)  # 予算も目標も無い
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(cpc_yen=120, cvr=0.02, target_roas=3)  # ROAS には売上が要る
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(cpc_yen=0, cvr=0.02, monthly_budget_yen=1000)
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(**{**BASE, "budget_multipliers": [1, -1]})


REAL = Path(kb.DEFAULT_KB_DIR)


@pytest.mark.skipif(not REAL.is_dir(), reason="実物の知識ベースが無い環境")
def test_実物_根拠の行がいまも知識ベースにある(monkeypatch):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(REAL))
    out = simulate.cpa_roas_simulate(**{**BASE, "saturation_spend_yen": 400000})
    bad = [c for c in out["根拠"] if "見つからない" in c["確かめ"] or "見当たらない" in c["確かめ"] or "読めなかった" in c["確かめ"]]
    bad = [c for c in bad if c.get("メモ") != "20"]  # 公開版の知識ベースに 20 は無い
    assert bad == [], json.dumps(bad, ensure_ascii=False, indent=1)
    assert {c["ファイル"].split("_")[0] for c in out["根拠"] if "ファイル" in c} >= {"00", "14", "37"}


# ---------------- 業種平均 ----------------

MINI_BENCH = {"retrieved": "2026-11-19", "sets": [
    {"id": "localiq_google_search_2026", "media": "Google広告（検索）", "region": "US", "period": "見本", "source_url": "https://example.com/g",
     "rows": [{"industry_en": "Real Estate", "industry_ja": "不動産", "ctr": 0.08, "cpc_usd": 2.0, "cvr": 0.03, "cpl_usd": 60.0},
              {"industry_en": "Apparel", "industry_ja": "アパレル", "ctr": 0.06, "cpc_usd": 1.0, "cvr": 0.04, "cpl_usd": None}]},
    {"id": "wordstream_facebook_leads_2025", "media": "Facebook広告", "region": "US", "period": "見本", "source_url": "https://example.com/f",
     "rows": [{"industry_en": "Real Estate", "industry_ja": "不動産", "ctr": 0.02, "cpc_usd": 1.5, "cvr": 0.1, "cpl_usd": 15.0}]}]}


@pytest.fixture
def bench(monkeypatch, tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(MINI_BENCH, ensure_ascii=False), encoding="utf-8")
    from ochinana_ads import benchmarks
    monkeypatch.setenv(benchmarks.ENV_FILE, str(p))


def test_業種平均を初期値に入れる(bench):
    out = simulate.cpa_roas_simulate(monthly_budget_yen=300000, industry="不動産")
    b = out["業種平均"]
    assert out["入力"]["クリック単価"] == 300 and out["入力"]["コンバージョン率"] == 0.03  # 2 ドル × 150 円
    assert b["目印"] == {"クリック単価": "業種平均で入れた", "コンバージョン率": "業種平均で入れた", "クリック率": "業種平均で入れた"}
    assert "日本の平均" in b["注意"] or "日本の実勢" in b["注意"]
    f = out["試算（この予算のとき）"]
    assert f["クリック数"] == 1000 and f["表示回数"] == 12500 and f["CPM"] == 24000  # 1,000 ÷ 0.08、300,000 ÷ 12,500 × 1,000
    out = simulate.cpa_roas_simulate(monthly_budget_yen=300000, cpc_yen=120, industry="real estate", benchmark_media="meta", usd_jpy=140)
    assert out["入力"]["クリック単価"] == 120 and out["入力"]["コンバージョン率"] == 0.1  # 自分の CPC は残す
    assert out["業種平均"]["cpc_yen"] == 210 and "渡された値" in out["業種平均"]["換算"]
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(monthly_budget_yen=300000, industry="宇宙")
    with pytest.raises(ValueError):
        simulate.cpa_roas_simulate(monthly_budget_yen=300000)  # CPC も業種も無い


REAL_BENCH = Path(kb.DEFAULT_KB_DIR).parent / "_ベンチマーク" / "benchmarks.json"


@pytest.mark.skipif(not REAL_BENCH.is_file(), reason="共有の業種平均のファイルが無い環境")
def test_実物_共有の業種平均のファイルを読める(monkeypatch):
    from ochinana_ads import benchmarks
    monkeypatch.delenv(benchmarks.ENV_FILE, raising=False)
    for media in ("google", "facebook"):
        b = benchmarks.lookup("不動産", media)
        assert b["cpc_yen"] and b["cvr"] and b["地域"] == "US"
