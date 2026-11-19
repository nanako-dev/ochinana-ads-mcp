"""レポートの数字の束（report_numbers）と、文章の数字の突き合わせ（report_number_check）。本物の API は呼ばない。"""
import json
from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import config, numcheck, report, tools

TODAY = date(2026, 11, 19)
CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))


def E(name):
    return NS(name=name)


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    yield
    tools._accounts.clear()


def api_row(cid, name, d, imp, clicks, yen, conv=0.0, value=0.0):
    return NS(campaign=NS(id=cid, name=name, status=E("ENABLED"), advertising_channel_type=E("SEARCH")),
              campaign_budget=NS(amount_micros=1000 * 1_000_000), segments=NS(date=d.isoformat()),
              metrics=NS(impressions=imp, clicks=clicks, cost_micros=int(yen * 1_000_000), conversions=conv,
                         conversions_value=value))


def fake_run(cur, prev, seen):
    """期間の条件を見て、今か比較期間の行を返す。"""
    def run(customer_id, query):
        assert query.startswith("SELECT ")
        seen.append(query)
        resource = query.split(" FROM ")[1].split()[0]
        if resource == "customer":
            return [CUSTOMER]
        rows = cur + prev
        lo = query.split("BETWEEN '")[1][:10]
        hi = query.split("AND '")[1][:10]
        return [r for r in rows if lo <= r.segments.date <= hi]
    return run


def week(start, cid, name, imp, clicks, yen, conv, value=0.0):
    return [api_row(cid, name, start + timedelta(days=i), imp, clicks, yen, conv, value) for i in range(7)]


CUR = week(date(2026, 11, 11), 1, "A", 1000, 50, 5000, 2) + week(date(2026, 11, 11), 2, "B", 500, 20, 3000, 1)
PREV = week(date(2026, 11, 4), 1, "A", 900, 40, 4400, 2)


def make(**kw):
    seen = []
    out = tools.report_numbers("1112223333", "2026-11-11", "2026-11-17", run=fake_run(CUR, PREV, seen), today=TODAY, **kw)
    return out, seen


def by(rows, name):
    return next(r for r in rows if r["指標"] == name)


# ---------------- 守り ----------------

def test_許可していないアカウントと今日以降を断る():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    with pytest.raises(ValueError, match="許可リスト"):
        tools.report_numbers("1234567890", "2026-11-01", "2026-11-07", run=run, today=TODAY)
    with pytest.raises(ValueError, match="昨日"):
        tools.report_numbers("1112223333", "2026-11-13", "2026-11-19", run=fake_run([], [], []), today=TODAY)
    with pytest.raises(ValueError, match="重なって"):
        tools.report_numbers("1112223333", "2026-11-11", "2026-11-17", compare_start="2026-11-15",
                             compare_end="2026-11-20", run=fake_run([], [], []), today=TODAY)
    with pytest.raises(ValueError, match="compare は"):
        report.compare_period(date(2026, 11, 1), date(2026, 11, 7), "先月")


def test_問い合わせは読むだけでメールを読まない():
    _, seen = make()
    assert all(q.startswith("SELECT ") for q in seen)
    assert not any("user_email" in q or "mutate" in q.lower() for q in seen)


# ---------------- 数字の束 ----------------

def test_先頭の欄と合計の数字を手で計算した値と比べる():
    out, _ = make()
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert "直前の同じ日数（7 日）" in out["期間"] and "2026-11-04〜2026-11-10" in out["期間"]
    t = out["合計"]
    assert by(t, "費用")["今"] == 56000 and by(t, "費用")["前"] == 30800 and by(t, "費用")["増減率（%）"] == 81.8
    assert by(t, "コンバージョン")["今"] == 21.0 and by(t, "コンバージョン")["前"] == 14.0
    assert by(t, "コンバージョン単価")["今"] == 2667 and by(t, "コンバージョン単価")["前"] == 2200
    cvr = by(t, "コンバージョン率")
    assert cvr["今"] == 4.29 and cvr["前"] == 5.0 and cvr["差"] == -0.71 and cvr["差の単位"] == "pt"
    # コンバージョン値が 0 なら ROAS を出さない（0% と書かない）
    assert not any(r["指標"] == "ROAS" for r in t)


def test_要因の分解は寄与の合計が100で_向きが逆の要因はマイナス():
    out, _ = make()
    cpa = next(r for r in out["要因の分解"] if r["指標"] == "コンバージョン単価")
    shares = {p["要因"]: p["寄与の割合（%）"] for p in cpa["要因"]}
    # CPA +21.2% = クリック単価 ×1.039 ÷ コンバージョン率 ×0.857。ln で分けると 20% と 80%
    assert cpa["変化率（%）"] == 21.2 and shares == {"クリック単価": 20, "コンバージョン率": 80}
    conv = next(r for r in out["要因の分解"] if r["指標"] == "コンバージョン")
    s = {p["要因"]: p["寄与の割合（%）"] for p in conv["要因"]}
    assert s["コンバージョン率"] < 0 and abs(sum(s.values()) - 100) <= 1
    assert "打ち消している" in conv["読み方"]


def test_変化がほぼ無いときは寄与の割合を出さない():
    rows = [dict(date="2026-11-11", key="1", name="A", impressions=1000, clicks=50, cost=5000, conversions=2, value=0)]
    prev = [dict(date="2026-11-10", key="1", name="A", impressions=1001, clicks=50, cost=5001, conversions=2, value=0)]
    b, _ = report.build(rows, prev, start=date(2026, 11, 11), end=date(2026, 11, 11), cstart=date(2026, 11, 10),
                        cend=date(2026, 11, 10), compare_label="x", today=TODAY)
    cost = next(r for r in b["要因の分解"] if r["指標"] == "費用")
    assert all("寄与の割合（%）" not in p for p in cost["要因"]) and "0.5%" in cost["読み方"]


def test_内訳に寄与と新しく出た印が付く():
    out, _ = make()
    rows = {r["対象"]: r for r in out["内訳"]}
    assert rows["A"]["費用の変化への寄与（%）"] == 17 and rows["B"]["費用の変化への寄与（%）"] == 83
    assert rows["B"]["印"].startswith("比較期間に実績なし")
    assert rows["A"]["費用の構成比（%）"] == 62.5


def test_成果の遅れ_直近7日は未確定と書く():
    out, _ = make()
    lag = out["成果の遅れ"]
    # 今日 11/19、lag 7 → 11/11 までが確定。11/12〜11/17 の 6 日が未確定、そこの成果は 6 日 × 3 件
    assert lag["未確定の日数"] == 6 and lag["未確定の日のコンバージョン"] == 18.0 and "暫定" in lag["注意"]
    assert out["日別"][0].get("成果") is None and out["日別"][1]["成果"].startswith("未確定")
    assert out["日別"][6]["費用の7日平均"] == 8000


def test_目標と計画の進み具合():
    out, _ = make(target_cpa_yen=2500, plan_cost_yen=240000, plan_conversions=90, plan_start="2026-11-01",
                  plan_end="2026-11-30")
    g = {r["項目"]: r for r in out["目標・計画との差"]}
    assert g["目標のコンバージョン単価"]["判定"] == "目標を超えている" and g["目標のコンバージョン単価"]["差"] == 167
    cost = g["計画の費用"]
    # 11/01〜11/17 の実績: 11/04〜11/10 の 30,800 円 ＋ 11/11〜11/17 の 56,000 円 ＝ 86,800 円。按分の計画 240,000×17/30 ＝ 136,000
    assert cost["経過分の実績"] == 86800 and cost["経過日数で按分した計画"] == 136000
    assert cost["進み具合（%）"] == 63.8 and cost["帯"].startswith("危険")


def test_ROASはコンバージョン値があるときだけ():
    cur = week(date(2026, 11, 11), 1, "A", 1000, 50, 5000, 2, value=20000)
    prev = week(date(2026, 11, 4), 1, "A", 1000, 50, 5000, 2, value=10000)
    out = tools.report_numbers("1112223333", "2026-11-11", "2026-11-17", target_roas=300,
                               run=fake_run(cur, prev, []), today=TODAY)
    roas = by(out["合計"], "ROAS")
    assert roas["今"] == 400 and roas["前"] == 200 and roas["差"] == 200.0 and roas["差の単位"] == "pt"
    dec = next(r for r in out["要因の分解"] if r["指標"] == "ROAS")
    assert {p["要因"]: p["寄与の割合（%）"] for p in dec["要因"]}["1件あたりの値"] == 100


def test_表はそのまま貼れる形で_束はファイルに残る():
    out, _ = make()
    assert out["表"]["合計"].splitlines()[0].startswith("| 指標 | 今（11/11〜11/17） | 前（11/04〜11/10）")
    assert "| 費用 | 56,000 円 | 30,800 円 | +25,200 円 | +81.8% |" in out["表"]["合計"]
    saved = json.loads(open(out["保存先"], encoding="utf-8").read())
    assert saved["束"]["束ID"] == out["束ID"] and len(saved["数字"]) == out["数字の数"]


def test_前年同期と7日前の比べ方():
    assert report.compare_period(date(2026, 11, 1), date(2026, 11, 30), "yoy")[:2] == (date(2025, 11, 1), date(2025, 11, 30))
    assert report.compare_period(date(2026, 11, 11), date(2026, 11, 17), "week")[:2] == (date(2026, 11, 4), date(2026, 11, 10))
    assert report.compare_period(date(2028, 2, 29), date(2028, 2, 29), "yoy")[0] == date(2027, 3, 1)


# ---------------- 文章の数字の突き合わせ ----------------

def test_突き合わせ_一致と束に無いと指標の取り違え():
    out, _ = make()
    text = ("今週の費用は 56,000 円で、前週より +81.8% 増えました。コンバージョン単価は 2,667 円（前 2,200 円）。"
            "CPA の悪化は主にクリック単価（+5.0%）です。CVR は -0.71pt。11/17 時点で約 5.6 万円。3 つの提案があります。"
            "費用は 99,999 円。")
    r = tools.report_number_check(text, out["束ID"])
    bad = {x["文字列"]: x["結果"] for x in r["直すもの"]}
    assert bad == {"+5.0%": "指標が違う疑い", "99,999 円": "束に無い"}
    ok = {x["文字列"] for x in r["一致"]}
    assert {"56,000 円", "+81.8%", "2,667 円", "2,200 円", "-0.71pt", "5.6 万円"} <= ok
    assert {"文字列": "3 つ", "理由": "数え（束の外）"} in r["見ていない"] and {"文字列": "11/17", "理由": "日付"} in r["見ていない"]
    assert r["件数"]["束に無い"] == 1 and r["件数"]["指標が違う疑い"] == 1


def test_突き合わせ_符号の向きとallow():
    out, _ = make()
    r = tools.report_number_check("費用は -81.8% でした。目標は 250 円。", out["束ID"], allow=[250])
    assert r["直すもの"][0]["結果"] == "符号が違う"
    assert r["件数"]["許可（allow）"] == 1


def test_突き合わせ_表の行は行の頭の指標で見る_日付と見出しは見ない():
    out, _ = make()
    text = "## 1. 今週\n" + out["表"]["合計"] + "\n2026-11-17 時点。"
    r = tools.report_number_check(text, out["束ID"])
    assert r["結果"] == "全部一致", r["直すもの"]


def test_突き合わせ_束IDの形と空の文章を断る():
    with pytest.raises(ValueError, match="束ID"):
        tools.report_number_check("費用 100 円", "../etc/passwd")
    with pytest.raises(ValueError, match="見つからない"):
        tools.report_number_check("費用 100 円", "R20261117-0123abcd")
    with pytest.raises(ValueError, match="空"):
        tools.report_number_check("  ", "R20261117-0123abcd")


def test_円の四捨五入は半分を上げ_足し算の誤差でずれない():
    from ochinana_ads import report as R
    rows = [dict(date="2026-11-11", key="1", name="A", impressions=1, clicks=1, cost=c, conversions=0, value=0)
            for c in (100.0, 200.2, 300.3, 400.0, 234.0)]
    assert R.sums(rows)["cost"] == 1234.5 and R.rnd("費用", R.sums(rows)["cost"]) == 1235
    assert R.hround(2.5) == 3 and R.hround(0.125, 2) == 0.13


def test_日別と内訳の表にも指標を網羅する():
    from ochinana_ads import report as R
    out, _ = make()
    for key in ("内訳", "日別"):
        head = out["表"][key].splitlines()[0]
        for m in R.CORE_METRICS:
            assert f" {m} |" in head, (key, m)
    assert "| A | 今 | 7,000 回 | 350 回 | 5.00% | 100 円 | 35,000 円 | 14.0 件 | 4.00% | 2,500 円 | +17% |" in out["表"]["内訳"]
    assert "|  | 前 | 6,300 回 | 280 回 | 4.44% | 110 円 | 30,800 円 | 14.0 件 | 5.00% | 2,200 円 |  |" in out["表"]["内訳"]
    d = out["日別"][0]
    assert all(m in d for m in R.CORE_METRICS) and d["クリック率"] == 4.67 and d["コンバージョン率"] == 4.29
