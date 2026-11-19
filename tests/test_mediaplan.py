"""予算の配分の試算（media_plan_simulate）と、近似曲線（fit_response_curve）。外にも API にもつながない。

元のスクリプト（環境変数 MEDIA_PLAN_ORIG_DIR）があれば、こちらで作った設定を両方に通して、同じ数字が出ることを確かめる
（元のスクリプトは読むだけ・動かすだけで、書き換えない。出力は一時フォルダに書く）。設定と点の数字は、このテストのために作ったもの。
"""
import os
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ochinana_ads import curvefit, kb, mediaplan

ORIG = Path(os.environ.get("MEDIA_PLAN_ORIG_DIR") or "/nonexistent")
M = 10_000

CONF_MAN = {
    "title": "テスト用の試算", "unit": "件", "months": ["1月", "2月", "3月", "4月", "5月", "6月"],
    "demand": [80, 100, 120, 100, 90, 110], "step_man": 5,
    "search_layers": [
        {"id": "brand", "name": "指名", "vol": 800, "share": 0.9, "ctr": 0.35, "cpc": 50, "cvr": 0.05, "brand": True},
        {"id": "core", "name": "意図の高い言葉", "vol": 4000, "share": 0.8, "ctr": 0.07, "cpc": 200, "cvr": 0.025},
        {"id": "mid", "name": "比較の言葉", "vol": 5000, "share": 0.5, "ctr": 0.03, "cpc": 150, "cvr": 0.01},
        {"id": "broad", "name": "関連語", "cap_man": 20, "cpc": 220, "cvr": 0.005}],
    "channels": [
        {"id": "remarketing", "name": "再配信", "cpc": 100, "cvr": 0.006, "sat_man": 20, "cap_man": 25, "seasonal": True},
        {"id": "social", "name": "SNS", "cpc": 50, "cvr": 0.0015, "sat_man": 30, "cap_man": 30, "seasonal": False}],
    "current": {"total_man": 400},
    "scenarios": {"A": {"total_man": 400, "name": "A"}, "B": {"ratio": 1.05, "name": "B"}, "C": {"ratio": 2.0, "name": "C"}},
    "targets": [100, 200, 400],
    "reach": {"population": 300000, "channels": {"social": {"cpm": 600, "on_target": 0.3}, "display": {"cpm": 250, "on_target": 0.1}},
              "budgets_man": [30, 100, 300]},
}


def to_yen(c):
    L = [{**{k: v for k, v in l.items() if k != "cap_man"}, **({"cap_yen": l["cap_man"] * M} if "cap_man" in l else {})}
         for l in c["search_layers"]]
    C = [{**{k: v for k, v in ch.items() if k not in ("cap_man", "sat_man")}, "cap_yen": ch["cap_man"] * M, "sat_yen": ch["sat_man"] * M}
         for ch in c["channels"]]
    sc = {k: ({"total_yen": v["total_man"] * M} if "total_man" in v else {"ratio": v["ratio"]}) | {"name": v["name"]}
          for k, v in c["scenarios"].items()}
    reach = {"population": c["reach"]["population"], "channels": c["reach"]["channels"],
             "budgets_yen": [x * M for x in c["reach"]["budgets_man"]]}
    return dict(months=c["months"], search_layers=L, channels=C, demand=c["demand"], current_total_yen=c["current"]["total_man"] * M,
                scenarios=sc, targets=c["targets"], reach=reach)


@pytest.fixture(autouse=True)
def _no_kb(monkeypatch, tmp_path):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(tmp_path / "no-kb"))
    monkeypatch.setenv("OCHINANA_ADS_OUT_DIR", str(tmp_path / "out"))


# ---------------- media_plan_simulate ----------------

def test_ひとつの媒体なら手で計算できる():
    # 鈍る形の媒体 1 つ・季節なし・上限 30 万円。5 万円ずつ足すと、件数 ＝ r S (1 − e^(−x/S))、r ＝ 0.01/100、S ＝ 20 万円
    out = mediaplan.media_plan_simulate(["1月"], channels=[{"id": "a", "cpc": 100, "cvr": 0.01, "sat_yen": 200000, "cap_yen": 300000}],
                                        current_total_yen=100000, targets=[5, 100])
    r, S = 0.01 / 100, 200000
    f = lambda x: r * S * (1 - math.exp(-x / S))  # noqa: E731
    assert out["上限まで積んだとき"] == {"総額": 300000, "件数": round(f(300000), 1)}
    a = out["案"]["A"]
    assert a["総額"] == 100000 and a["件数"] == round(f(100000), 1)
    assert a["最後の 1 回分の 1 件あたり"] == round(50000 / (f(100000) - f(50000)))
    assert out["案"]["C"]["届く"] is False and "届かない" in out["案"]["C"]["注記"]
    inv = {r["件数"]: r for r in out["逆算"]}
    assert inv[5]["要る媒体費"] == 100000 and inv[100]["要る媒体費"].startswith("届かない")


def test_検索の層は意図の高い順に上限まで():
    out = mediaplan.media_plan_simulate(["1月"], search_layers=[
        {"id": "brand", "vol": 1000, "share": 1, "ctr": 0.5, "cpc": 40, "cvr": 0.1, "brand": True},  # 500 クリック・2 万円・50 件
        {"id": "core", "vol": 2000, "share": 1, "ctr": 0.1, "cpc": 200, "cvr": 0.02}], step_yen=10000)  # 200 クリック・4 万円・4 件
    assert out["上限まで積んだとき"] == {"総額": 60000, "件数": 54.0}
    out = mediaplan.media_plan_simulate(["1月"], search_layers=[
        {"id": "brand", "vol": 1000, "share": 1, "ctr": 0.5, "cpc": 40, "cvr": 0.1, "brand": True},
        {"id": "core", "vol": 2000, "share": 1, "ctr": 0.1, "cpc": 200, "cvr": 0.02}], step_yen=10000,
        scenarios={"X": {"total_yen": 40000}})
    x = out["案"]["X"]
    assert x["件数"] == 52.0 and x["指名以外の件数"] == 2.0  # 指名 2 万円で 50 件、残り 2 万円で意図の高い層の半分＝2 件


def test_上限の無い媒体と入力の誤り(tmp_path):
    out = mediaplan.media_plan_simulate(["1月", "2月"], channels=[{"id": "a", "cpc": 100, "cvr": 0.01}], save_chart=True)
    notes = " ".join(out["前提"]["仮置き・補ったもの"])
    assert "5 倍" in notes and "500,000 円とした" in notes
    assert out["上限まで積んだとき"]["総額"] == 2 * 5 * 500000
    assert Path(out["図"]).is_file() and Path(out["図"]).read_text(encoding="utf-8").startswith("<svg")
    with pytest.raises(ValueError):
        mediaplan.media_plan_simulate([], channels=[{"id": "a", "cpc": 100, "cvr": 0.01}])
    with pytest.raises(ValueError):
        mediaplan.media_plan_simulate(["1月"], channels=[{"id": "a", "cpc": 100, "cvr": 2}])
    with pytest.raises(ValueError):
        mediaplan.media_plan_simulate(["1月", "2月"], channels=[{"id": "a", "cpc": 100, "cvr": 0.01}], demand=[1])
    with pytest.raises(ValueError):
        mediaplan.media_plan_simulate(["1月"], search_layers=[{"id": "x", "cpc": 100, "cvr": 0.01}])  # vol か cap_yen が要る
    with pytest.raises(ValueError):
        mediaplan.media_plan_simulate(["1月"])


def test_届く割合():
    t = mediaplan.reach_table(100000, {"a": {"cpm": 1000, "on_target": 1}}, [100000])
    # 表示 100,000 回、人数 10 万 → 1 − e^−1 ＝ 63.2%、平均 1.6 回
    assert t["行"][0]["届く割合（%）"] == 63.2 and t["行"][0]["平均の回数"] == 1.6


@pytest.mark.skipif(not (ORIG / "media_plan.py").is_file(), reason="元のスクリプトが無い環境")
def test_元のmedia_planと同じ数字が出る(tmp_path):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps(CONF_MAN, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(ORIG / "media_plan.py"), str(conf), str(tmp_path / "o.json")], check=True,
                   capture_output=True, timeout=300, cwd=str(tmp_path))
    o = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    n = mediaplan.media_plan_simulate(**to_yen(CONF_MAN))
    man = lambda y: round(y / M, 1)  # noqa: E731  元は金額を 0.1 万円（1,000 円）に丸めて持つ
    assert man(n["上限まで積んだとき"]["総額"]) == o["max_man"] and n["上限まで積んだとき"]["件数"] == round(o["max_conv"], 1)
    assert n["いま"]["件数"] == o["current"]["conv"]
    for k in ("A", "B"):
        a, b = o["scenarios"][k], n["案"][k]
        assert man(b["総額"]) == a["total_man"] and b["件数"] == a["conv"] and b["最後の 1 回分の 1 件あたり"] == a["last_step_cpa_yen"]
    assert o["scenarios"]["C"]["reachable"] is False and n["案"]["C"]["届く"] is False
    assert [(r["need_man"], r["last_step_cpa_yen"]) for r in o["inverse"]][:2] == \
        [(man(r["要る媒体費"]), r["そのときの最後の 1 回分の 1 件あたり"]) for r in n["逆算"][:2]]
    assert [(r["reach_pct"], r["freq"]) for r in o["reach"]["rows"]] == [(r["届く割合（%）"], r["平均の回数"]) for r in n["届く割合"]["行"]]
    assert [(c["spent_man"], c["conv"], c["last_cpa_yen"]) for c in o["curve"]] == \
        [(man(c["総額"]), c["件数"], c["最後の 1 回分の 1 件あたり"]) for c in n["予算と件数の曲線（間引き）"]]


# ---------------- fit_response_curve ----------------

COST = [80000, 150000, 220000, 300000, 380000, 450000, 520000]


def test_比例の点なら比例を選ぶ():
    out = curvefit.fit_response_curve(COST, [c * 0.01 for c in COST], "クリック数")
    assert out["選んだ形"] == "比例" and out["当てた形"]["比例"]["a"] == 0.01 and out["当てた形"]["比例"]["決定係数"] == 1.0


def test_鈍る点なら鈍る形を選びSを返す():
    S, c = 250000, 3000
    out = curvefit.fit_response_curve(COST, [c * (1 - math.exp(-x / S)) for x in COST], "成果の件数")
    assert out["選んだ形"].startswith("鈍る")
    got = out["当てた形"]["鈍る"]["S"]
    assert abs(got - S) / S < 0.02  # 400 通りの目の細かさの範囲で一致
    assert out["ほかの道具へ渡すなら"]["鈍る形の S（鈍り始める額）"] == round(got)


def test_点が少ない_ばらつく_口座から読む():
    out = curvefit.fit_response_curve([50000, 100000, 0, 150000], [2000, 3900, 0, 6100])
    assert "3 つしかない" in out["結果"]
    out = curvefit.fit_response_curve([100000, 200000, 300000, 400000, 500000], [900, 400, 1500, 300, 800])
    assert any("決定係数 0.3 未満" in n for n in out["読み方"])
    rows = [SimpleNamespace(segments=SimpleNamespace(month=f"2026-0{m}-01"),
                            metrics=SimpleNamespace(cost_micros=int(c * 1e6), clicks=int(c * 0.01), impressions=int(c * 0.3), conversions=c * 0.0002))
            for m, c in zip(range(1, 8), COST)]
    out = curvefit.fit_response_curve(customer_id="4445556666", start="2026-01-01", end="2026-07-31", value_name="clicks",
                                      run=lambda cid, q: rows)
    assert out["点の出どころ"].startswith("Google 広告 4445556666") and len(out["点"]) == 7 and out["選んだ形"] == "比例"
    with pytest.raises(ValueError):
        curvefit.fit_response_curve(customer_id="1234567890", start="2026-01-01", end="2026-02-01", run=lambda c, q: [])


@pytest.mark.skipif(not (ORIG / "fit_curve.py").is_file(), reason="元のスクリプトが無い環境")
def test_元のfit_curveと同じ数字が出る(tmp_path):
    pts = {"media": {"検索": {"cost": COST, "click": [700, 1250, 1650, 2000, 2250, 2400, 2550]},
                     "ばらつく": {"cost": [100000, 200000, 300000, 400000, 500000], "click": [900, 400, 1500, 300, 800]}}}
    src = tmp_path / "p.json"
    src.write_text(json.dumps(pts, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(ORIG / "fit_curve.py"), str(src), str(tmp_path / "f.json"), "--y", "click"], check=True,
                   capture_output=True, timeout=120, cwd=str(tmp_path))
    o = json.loads((tmp_path / "f.json").read_text(encoding="utf-8"))["media"]
    for name, m in pts["media"].items():
        n = curvefit.fit_response_curve(m["cost"], m["click"], "クリック数")["当てた形"]
        assert n["比例"]["a"] == round(o[name]["linear"]["a"], 6)
        assert n["頭打ち"]["c"] == round(o[name]["saturating"]["c"], 2) and n["頭打ち"]["b"] == round(o[name]["saturating"]["b"], 2)
        assert n["頭打ち"]["決定係数"] == round(o[name]["saturating"]["r2"], 3) and n["比例"]["決定係数"] == round(o[name]["linear"]["r2"], 3)


REAL = Path(kb.DEFAULT_KB_DIR)


@pytest.mark.skipif(not REAL.is_dir(), reason="実物の知識ベースが無い環境")
def test_実物_根拠の行がいまも知識ベースにある(monkeypatch):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(REAL))
    outs = [mediaplan.media_plan_simulate(**to_yen(CONF_MAN)), curvefit.fit_response_curve(COST, [c * 0.01 for c in COST])]
    cites = [c for o in outs for c in o["根拠"]]
    bad = [c for c in cites if "見つからない" in c["確かめ"] or "読めなかった" in c["確かめ"]]
    bad = [c for c in bad if c.get("メモ") != "20"]  # 公開版の知識ベースに 20 は無い
    assert bad == [], json.dumps(bad, ensure_ascii=False, indent=1)
