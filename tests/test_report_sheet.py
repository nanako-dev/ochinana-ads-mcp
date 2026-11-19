"""レポートのスプレッドシート版（report_sheet_plan・report_sheet_check）。シートには書かない。"""
from datetime import date

import pytest

from ochinana_ads import config, numcheck, report, sheet, tools

TODAY = date(2026, 11, 19)


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path / "out"))
    monkeypatch.setenv("OCHINANA_ADS_LOG_DIR", str(tmp_path / "log"))


def make():
    cur = [dict(date=f"2026-11-{11 + i:02d}", key="1", name="A", media="Google 広告", impressions=100, clicks=10,
                cost=1000.4, conversions=1 if i % 2 else 0, value=0) for i in range(7)]
    prev = [dict(date=f"2026-11-{4 + i:02d}", key="1", name="A", media="Google 広告", impressions=90, clicks=9, cost=900,
                 conversions=1, value=0) for i in range(7)]
    b, nums = report.build(cur, prev, start=date(2026, 11, 11), end=date(2026, 11, 17), cstart=date(2026, 11, 4),
                           cend=date(2026, 11, 10), compare_label="直前", today=TODAY,
                           head={"対象アカウント": "1112223333（テスト）"})
    numcheck.save_bundle(b, nums, rows=prev + cur)
    return b


MD = """# 週次
## 1. いちばん大事なこと
- 【事実】費用は 7,003 円。
## 8. ネクストアクション
1. **確かめる: 内訳を見る**
   - なぜ: 少ない
   - 段: 確かめる／誰の案: AI の提案（未承認）／承認: 不要
   - いつ・何で評価: 今週
"""


def test_元のデータは値で_合計と率は式(tmp_path):
    b = make()
    md = tmp_path / "w.md"
    md.write_text(MD, encoding="utf-8")
    p = tools.report_sheet_plan(b["束ID"], str(md))
    assert list(p)[:3] == ["対象アカウント", "期間", "単位"]
    vals = p["書く値"]
    data = vals["'日別データ'!A1"]
    assert data[0][:4] == ["日付", "媒体", "キャンペーン ID", "キャンペーン"] and len(data) == 15
    assert data[1][2] == "'1"  # ID は文字のまま
    week = vals["'週次_2026-11-17'!A1"]
    cells = [c for r in week for c in r if isinstance(c, str)]
    sums = [c for c in cells if c.startswith("=SUMIFS(")]
    assert sums and all("'日別データ'!$A:$A" in c for c in sums)
    # 割り算は 0 で守る
    assert any(c.startswith('=IF(B') and '"—"' in c for c in cells)
    # 比べる期間は入力から式で出す
    assert "=B4-(B5-B4+1)" in cells and "=B4-1" in cells
    # 文とネクストアクションが入る
    assert "【事実】費用は 7,003 円。" in cells and "確かめる: 内訳を見る" in cells
    assert ["順", "何をする", "なぜ", "段", "誰の案", "承認", "いつ・何で評価"] in week
    # 先に送る要求はタブ作り、あとの要求にグラフと注記
    first = p["先に送る要求"]
    assert sum("addSheet" in r for r in first) == 3 and any("updateSheetProperties" in r for r in first)
    later = p["あとで送る要求"]
    assert any("addChart" in r for r in later) and any(r.get("updateCells", {}).get("fields") == "note" for r in later)
    assert p["確かめる範囲"]["合計の範囲"].startswith("'週次_2026-11-17'!A11:E")


def test_シートの合計を束と照らす():
    b = make()
    t = {r["指標"]: r for r in b["合計"]}
    good = [["指標", "今", "前"], ["表示回数", "700", "630"], ["クリック数", "70", "63"], ["費用", "7,003", "6,300"],
            ["コンバージョン", "3.0", "7.0"], ["クリック率", "10.00%", "10.00%"], ["クリック単価", "100", "100"],
            ["コンバージョン率", "4.29%", "11.11%"], ["コンバージョン単価", "2,334", "900"]]
    assert t["費用"]["今"] == 7003 and t["コンバージョン単価"]["今"] == 2334
    r = tools.report_sheet_check(b["束ID"], good)
    assert r["結果"] == "全部一致", r
    bad = [row[:] for row in good]
    bad[3][1] = "7,004"
    r = tools.report_sheet_check(b["束ID"], bad)
    assert r["合わない"][0]["指標"] == "費用"
    with pytest.raises(ValueError, match="values"):
        tools.report_sheet_check(b["束ID"], "x")


def test_元の行の無い古い束は断る():
    b = make()
    data = numcheck.load_file(b["束ID"])
    numcheck.save_bundle(data["束"], data["数字"])
    with pytest.raises(ValueError, match="元の行"):
        tools.report_sheet_plan(b["束ID"])


def test_ネクストアクションの読み取り():
    acts = sheet.md_actions(MD.split("## 8. ネクストアクション")[1].splitlines())
    assert acts == [{"順": 1, "何をする": "確かめる: 内訳を見る", "なぜ": "少ない", "段": "確かめる", "誰の案": "AI の提案（未承認）",
                     "承認": "不要", "いつ・何で評価": "今週"}]


def test_日別とキャンペーン別にも指標を網羅する():
    from ochinana_ads import report as R
    b = make()
    week = tools.report_sheet_plan(b["束ID"])["書く値"]["'週次_2026-11-17'!A1"]
    daily_head = next(r for r in week if r and r[0] == "日付")
    camp_head = next(r for r in week if r and r[0] == "キャンペーン")
    assert daily_head[1:9] == R.CORE_METRICS and camp_head[2:10] == R.CORE_METRICS
    i = week.index(camp_head)
    assert [week[i + 1][1], week[i + 2][1]] == ["今", "前"]
    # クリック率はその行のクリック ÷ 表示
    assert week[i + 1][4] == f'=IF(C{i + 2}=0,"—",D{i + 2}/C{i + 2})'


def test_照らしは日別とキャンペーン別も見る():
    b = make()
    d = b["日別"][0]
    head = ["日付", "表示回数", "クリック数", "クリック率", "費用"]
    row = [d["日付"], f"{d['表示回数']:,}", f"{d['クリック数']:,}", f"{d['クリック率']:.2f}%", f"{d['費用']:,}"]
    r = tools.report_sheet_check(b["束ID"], [head, row])
    assert r["照らした数"] == 4 and not r["合わない"]
    row[4] = "9,999"
    r = tools.report_sheet_check(b["束ID"], [head, row])
    assert r["合わない"][0]["表"] == f"日別 {d['日付']}" and r["合わない"][0]["指標"] == "費用"
    c = b["内訳"][0]
    r = tools.report_sheet_check(b["束ID"], [["キャンペーン", "期間", "費用", "クリック率"],
                                             [c["対象"], "今", f"{c['費用（今）']:,}", f"{c['クリック率（今）']:.2f}%"],
                                             [c["対象"], "前", f"{c['費用（前）']:,}", "1.00%"]])
    assert len(r["合わない"]) == 1 and r["合わない"][0]["表"] == "A（前）"


def test_日別データにも率と単価の列_行が増えても計算する():
    b = make()
    data = tools.report_sheet_plan(b["束ID"])["書く値"]["'日別データ'!A1"]
    head = data[0]
    assert head[:9] == sheet.DATA_HEAD
    extra = head[9:]
    names = [h.split('"')[1] for h in extra]
    assert names == ["クリック率", "クリック単価", "コンバージョン率", "コンバージョン単価"]  # 値が 0 なので ROAS は無し
    assert all("ARRAYFORMULA" in h and "LEN($A$2:$A)=0" in h and '"—"' in h for h in extra)
    assert all(r[9:] == [None] * 4 for r in data[1:])  # 元の行の率の列は空けて、式に任せる
