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



def _cells(grid):
    return [c for r in grid for c in r if isinstance(c, str)]


def test_元のデータは値で_サマリーと日別は設定を指す式(tmp_path):
    b = make()
    md = tmp_path / "w.md"
    md.write_text(MD, encoding="utf-8")
    p = tools.report_sheet_plan(b["束ID"], str(md), levels=False)
    assert list(p)[:3] == ["対象アカウント", "期間", "単位"]
    vals = p["書く値"]
    data = vals["'日別データ'!A1"]
    assert data[0][:4] == ["日付", "媒体", "キャンペーン ID", "キャンペーン"] and len(data) == 15
    assert data[1][2] == "'1"  # ID は文字のまま
    # タブの並び（levels=False なので階層は キャンペーン別 だけ、週別の推移は無い）
    assert p["確かめる範囲"]["タブの並び"] == ["サマリー", "日別", "キャンペーン別", "判断の記録", "設定", "日別データ",
                                         "日別データ_詳細", "データの出どころ"]
    assert "'週次_2026-11-17'!A1" not in vals
    # 設定: 入力と、前週は入力からの式
    st = vals["'設定'!A1"]
    assert st[3][:2] == ["今週の初日", "2026-11-11"] and st[4][:2] == ["今週の最終日", "2026-11-17"]
    assert st[5][1] == "=B4-(B5-B4+1)" and st[6][1] == "=B4-1"
    # サマリー: 題に ID を出さない。期間の文と数字は全部 設定 を指す式
    sm = vals["'サマリー'!A1"]
    assert sm[1][1] == "テスト　週次レポート"
    assert sm[2][1].startswith("=TEXT('設定'!$B$4") and "'設定'!$B$6" in sm[2][1]
    cells = _cells(sm)
    sums = [c for c in cells if "SUMIFS(" in c]
    assert sums and all("'日別データ'!$A:$A" in c and "'設定'!$B$" in c for c in sums)
    calc_head = next(r for r in sm if "指標" in r)
    i = calc_head.index("指標")
    assert calc_head[i:i + 5] == ["指標", "今週", "前週", "差", "増減率"]
    names = [r[i] for r in sm[5:13]]
    assert names == report.CORE_METRICS
    # カードは元の数字のセルを指し、比べる文は前の週の値つき
    assert sm[4][1] == "費用" and sm[5][1] == "=Q10" and "前の週" in sm[6][1]
    assert sm[8][1:5] == ["表示回数", None, None, "クリック数"]
    # まとめ（【事実】を外す）と次にやること
    assert "費用は 7,003 円。" in cells and "確かめる: 内訳を見る" in cells
    assert any(r[1:12:2][:1] == ["何をする"] or (len(r) > 11 and r[1] == "何をする" and r[11] == "いつ・何で見る") for r in sm if r)
    # 先に送る要求はタブ作りと名前付き範囲、あとの要求にグラフ・注記・タブの並び
    first = p["先に送る要求"]
    assert sum("addSheet" in r for r in first) == 8 and sum("addNamedRange" in r for r in first) == 8
    assert not any("deleteSheet" in r for r in first)
    later = p["あとで送る要求"]
    assert any("addChart" in r for r in later) and any(r.get("updateCells", {}).get("fields") == "note" for r in later)
    assert any(r.get("addChart", {}).get("chart", {}).get("spec", {}).get("basicChart", {}).get("chartType") == "COMBO" for r in later)
    # 字は Noto Sans JP、フォントの指定を上書きしない（textFormat 全体の上書きをしない）
    assert any(json.dumps(r, ensure_ascii=False).count("Noto Sans JP") for r in later)
    assert not any(r.get("repeatCell", {}).get("fields", "").endswith("userEnteredFormat.textFormat") for r in later)


def test_シートの合計を束と照らす():
    b = make()
    t = {r["指標"]: r for r in b["合計"]}
    good = [["指標", "今週", "前週"], ["表示回数", "700", "630"], ["クリック数", "70", "63"], ["費用", "7,003", "6,300"],
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


def test_サマリーのカードも照らす():
    b = make()
    rows = [["費用", "", "", "コンバージョン", "", "", "コンバージョン単価", "", "", "コンバージョン率"],
            ["7,003 円", "", "", "3.0 件", "", "", "2,334 円", "", "", "4.29%"],
            ["▲ 11%（前の週 6,300 円）"], [],
            ["表示回数", "", "", "クリック数", "", "", "クリック率", "", "", "クリック単価"],
            ["700 回", "", "", "70 回", "", "", "10.00%", "", "", "100 円"]]
    r = tools.report_sheet_check(b["束ID"], rows)
    assert r["結果"] == "全部一致" and r["照らした数"] == 8, r
    rows[1][0] = "7,004 円"
    r = tools.report_sheet_check(b["束ID"], rows)
    assert [x["指標"] for x in r["合わない"]] == ["費用"] and r["合わない"][0]["表"] == "サマリーのカード"


def test_元の行の無い古い束は断る():
    b = make()
    data = numcheck.load_file(b["束ID"])
    numcheck.save_bundle(data["束"], data["数字"])
    with pytest.raises(ValueError, match="元の行"):
        tools.report_sheet_plan(b["束ID"], levels=False)


def test_ネクストアクションの読み取り():
    acts = sheet.md_actions(MD.split("## 8. ネクストアクション")[1].splitlines())
    assert acts == [{"順": 1, "何をする": "確かめる: 内訳を見る", "なぜ": "少ない", "段": "確かめる", "誰の案": "AI の提案（未承認）",
                     "承認": "不要", "いつ・何で評価": "今週"}]


def test_日別のタブは全部の指標_照らしも見る():
    b = make()
    p = tools.report_sheet_plan(b["束ID"], levels=False)
    dly = p["書く値"]["'日別'!A1"]
    head = next(r for r in dly if r and r[0] == "日付")
    assert head[2:10] == report.CORE_METRICS and head[-1] == "成果の数字"
    first = dly[dly.index(head) + 1]
    assert first[0] == "='設定'!$B$4" and "'設定'!$B$8" in first[-1]
    later = p["あとで送る要求"]
    titles = [r["addChart"]["chart"]["spec"].get("title") for r in later if "addChart" in r]
    assert {"日別のコンバージョン", "日別のコンバージョン率", "日別のコンバージョン単価"} <= set(titles)
    assert any("WEEKDAY" in json.dumps(r, ensure_ascii=False) for r in later)
    d = b["日別"][0]
    hd = ["日付", "曜日", "表示回数", "クリック数", "クリック率", "費用"]
    row = ["11/11", "水", f"{d['表示回数']:,}", f"{d['クリック数']:,}", f"{d['クリック率']:.2f}%", f"{d['費用']:,}"]
    r = tools.report_sheet_check(b["束ID"], [hd, row])
    assert r["照らした数"] == 4 and not r["合わない"]
    row[5] = "9,999"
    r = tools.report_sheet_check(b["束ID"], [hd, row])
    assert r["合わない"][0]["表"] == f"日別 {d['日付']}" and r["合わない"][0]["指標"] == "費用"


def test_日別データにも率と単価の列_行が増えても計算する():
    b = make()
    data = tools.report_sheet_plan(b["束ID"], levels=False)["書く値"]["'日別データ'!A1"]
    head = data[0]
    assert head[:9] == sheet.DATA_HEAD
    extra = head[9:]
    names = [h.split('"')[1] for h in extra]
    assert names == ["クリック率", "クリック単価", "コンバージョン率", "コンバージョン単価"]  # 値が 0 なので ROAS は無し
    assert all("ARRAYFORMULA" in h and "LEN($A$2:$A)=0" in h and '"—"' in h for h in extra)
    assert all(r[9:] == [None] * 4 for r in data[1:])  # 元の行の率の列は空けて、式に任せる


def test_値の小さいアカウントはコンバージョン値の列を出さない():
    b = {"合計": [{"指標": "ROAS", "今": 2.3, "前": None}]}
    assert not sheet.show_value(b)
    assert sheet.show_value({"合計": [{"指標": "ROAS", "今": 320.0, "前": 0}]})
    assert sheet.col(0) == "A" and sheet.col(25) == "Z" and sheet.col(26) == "AA"


# ---------------- 階層ごとのタブと週別の推移（読むだけの偽の API） ----------------

import json  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402


def _detail_run(seen):
    def E(n):
        return NS(name=n)

    def M(imp, clk, yen, cv):
        return NS(impressions=imp, clicks=clk, cost_micros=int(yen * 1_000_000), conversions=cv, conversions_value=0.0)

    def run(cid, query):
        assert query.startswith("SELECT ") and "segments.date" in query
        seen.append(query)
        lo = query.split("BETWEEN '")[1][:10]
        hi = query.split("AND '")[1][:10]
        rows = []
        if " FROM campaign WHERE" in query:
            d = date.fromisoformat(lo)
            while d <= date.fromisoformat(hi):
                rows.append(NS(campaign=NS(id=1, name="A", status=E("ENABLED"), advertising_channel_type=E("SEARCH")),
                               campaign_budget=NS(amount_micros=1_500_000_000), segments=NS(date=d.isoformat()),
                               metrics=M(50, 5, 500, 0.5)))
                d = date.fromordinal(d.toordinal() + 1)
            return rows
        for d, k in (("2026-11-12", 1), ("2026-11-05", 2)):
            if not (lo <= d <= hi):
                continue
            base = dict(campaign=NS(id=1, name="A"), ad_group=NS(id=10, name="グループ1"), segments=NS(date=d))
            if " FROM ad_group WHERE" in query:
                rows.append(NS(**base, metrics=M(100 * k, 10 * k, 1000 * k, 1)))
            elif " FROM ad_group_ad " in query:
                ad = NS(id=77, type_=E("RESPONSIVE_SEARCH_AD"), responsive_search_ad=NS(headlines=[NS(text="見出し")]))
                rows.append(NS(**base, ad_group_ad=NS(ad=ad), metrics=M(100 * k, 10 * k, 1000 * k, 1)))
            elif " FROM keyword_view " in query:
                crit = NS(criterion_id=5, keyword=NS(text="ホッケー 観戦", match_type=E("PHRASE")))
                rows.append(NS(**base, ad_group_criterion=crit, metrics=M(60 * k, 6 * k, 600 * k, 1)))
            else:
                rows.append(NS(**base, search_term_view=NS(search_term="ホッケー 観戦 ルール"), metrics=M(30 * k, 3 * k, 300 * k, 0)))
        return rows
    return run


def _full_plan(**kw):
    b = make()
    seen = []
    p = tools.report_sheet_plan(b["束ID"], run=_detail_run(seen), **kw)
    return b, p, seen


def test_階層のタブは_今週と前週を横に並べ_全部の指標と絞り込み():
    b, p, seen = _full_plan()
    assert len(seen) == 9 and all(q.startswith("SELECT ") for q in seen)
    assert not any(w in q.lower() for q in seen for w in ("mutate", "insert", "update ", "delete"))
    assert p["確かめる範囲"]["タブの並び"] == sheet.TAB_ORDER
    vals = p["書く値"]
    later = p["あとで送る要求"]
    filters = {r["setBasicFilter"]["filter"]["range"]["sheetId"]: r["setBasicFilter"]["filter"] for r in later if "setBasicFilter" in r}
    for level, t, sid in sheet.LEVEL_TABS:
        g = vals[f"'{t}'!A1"]
        head = g[5]
        nI = len(sheet.IDENT[level])
        assert head[:nI] == [h for h, _ in sheet.IDENT[level]]
        assert head[nI:nI + 8] == report.CORE_METRICS and head[nI + 8:nI + 16] == report.CORE_METRICS
        assert head[nI + 16:] == ["費用の差", "コンバージョンの差", "コンバージョン単価の増減率", "費用の構成比", "ID"]
        assert g[4][nI].startswith('="今週（"') and g[4][nI + 8].startswith('="前週（"') and g[4][nI + 16] == "前週比"
        body = g[6:]
        assert len(body) == 1  # 1 対象 1 行（今と前を縦に積まない）
        row = body[0]
        assert "D_LEVEL" in row[nI] and "D_ID" in row[nI] and "'設定'!$B$4" in row[nI] and "'設定'!$B$6" in row[nI + 8]
        assert row[-1].startswith("'")
        f = filters[sid]
        assert f["range"]["startRowIndex"] == 5 and f["range"]["endRowIndex"] == 7
        assert f["sortSpecs"] == [{"dimensionIndex": nI + 4, "sortOrder": "DESCENDING"}]
    # キャンペーンの行も 日別データ_詳細 に入る（4 階層 × 2 日 ＋ キャンペーン 14 日）
    det = vals["'日別データ_詳細'!A1"]
    assert det[0][:13] == sheet.DETAIL_HEAD and len(det) == 1 + 8 + 14
    assert sum(1 for r in det[1:] if r[2] == "キャンペーン") == 14
    # 前週比の塊は枠で囲む、色の濃さもある
    assert sum(1 for r in later if "updateBorders" in r and r["updateBorders"].get("left", {}).get("style") == "SOLID_MEDIUM") >= 6
    assert any("gradientRule" in r.get("addConditionalFormatRule", {}).get("rule", {}) for r in later)
    # データのタブは灰色の札
    adds = [r["addSheet"]["properties"] for r in p["先に送る要求"] if "addSheet" in r]
    assert all(("tabColorStyle" in a) == (a["title"] in sheet.DATA_TABS) for a in adds)


def test_週別の推移は直近8週_前週比の行つき():
    b, p, _ = _full_plan()
    tg = p["書く値"]["'週別の推移'!A1"]
    assert tg[3][0] == "週の初日" and tg[3][1] == "='設定'!$B$9" and len(tg[3]) == 9
    assert tg[5][0] == "指標＼週"
    names = [r[0] for r in tg[6:]]
    assert names[::2] == report.CORE_METRICS and all(n == "　前週比" for n in names[1::2])
    assert tg[7][1] == "—" and tg[7][2].startswith("=IF(AND(ISNUMBER(C7)")
    data = p["書く値"]["'日別データ'!A1"]
    dates = sorted({r[0] for r in data[1:]})
    assert dates[0] == "2026-09-23" and dates[-1] == "2026-11-17" and len(dates) == 56
    # 束の 2 週は束の行を使う（読み直した行と混ぜない）
    assert sum(1 for r in data[1:] if r[0] == "2026-11-12") == 1 and [r for r in data[1:] if r[0] == "2026-11-12"][0][6] == 1000.4
    later = p["あとで送る要求"]
    titles = [r["addChart"]["chart"]["spec"].get("title") for r in later if "addChart" in r]
    assert "週別の費用とコンバージョン" in titles and "週別のコンバージョン単価" in titles
    # 照らす: 古い週は偽の API の 7 日ぶん、新しい週は束の行
    rows = [["週の初日", "2026-09-23", "2026-11-04", "2026-11-11"], ["週の最終日", "2026-09-29", "2026-11-10", "2026-11-17"],
            ["指標＼週", "9/23〜9/29", "11/4〜11/10", "11/11〜11/17"],
            ["表示回数", "350", "630", "700"], ["　前週比", "—", "+0.0%", "+11.1%"],
            ["費用", "3,500", "6,300", "7,003"], ["コンバージョン単価", "1,000", "900", "2,334"]]
    r = tools.report_sheet_check(b["束ID"], rows)
    assert r["結果"] == "全部一致" and r["照らした数"] == 9, r
    rows[5][1] = "3,501"
    r = tools.report_sheet_check(b["束ID"], rows)
    assert len(r["合わない"]) == 1 and r["合わない"][0]["表"] == "週別 2026-09-23〜2026-09-29"


def test_階層のタブの今週と前週を照らす_違う数字を見つける():
    b, p, _ = _full_plan()
    head = p["書く値"]["'広告グループ別'!A1"][5]
    cur = ["100", "10", "10.00%", "100", "1,000", "1.0", "10.00%", "1,000"]
    prev = ["200", "20", "10.00%", "100", "2,000", "1.0", "5.00%", "2,000"]
    rows = [head, ["A", "グループ1", *cur, *prev, "-1,000", "0.0", "-50.0%", "100.0%", "10"]]
    r = tools.report_sheet_check(b["束ID"], rows)
    assert r["結果"] == "全部一致" and r["照らした数"] == 16, r
    rows[1][2 + 8 + 4] = "2,001"   # 前週の費用
    r = tools.report_sheet_check(b["束ID"], rows)
    assert [(x["表"], x["指標"]) for x in r["合わない"]] == [("広告グループ グループ1（前週）", "費用")]
    # キャンペーン別も同じ仕組み（キャンペーンの行は束の元の行から）
    ch = p["書く値"]["'キャンペーン別'!A1"][5]
    rows = [ch, ["A", "700", "70", "10.00%", "100", "7,003", "3.0", "4.29%", "2,334",
                 "630", "63", "10.00%", "100", "6,300", "7.0", "11.11%", "900", "+703", "-4.0", "+159.3%", "100.0%", "1"]]
    r = tools.report_sheet_check(b["束ID"], rows)
    assert r["結果"] == "全部一致" and r["照らした数"] == 16, r


def test_読んだ行は束に残して使い回す_作り直しの要求():
    b, p, seen = _full_plan()
    seen2 = []
    p2 = tools.report_sheet_plan(b["束ID"], run=_detail_run(seen2), replace_sheet_ids="0,1020", replace_named_range_ids="123")
    assert seen2 == []  # 2 回目は API を呼ばない
    first = p2["先に送る要求"]
    kinds = [next(iter(r)) for r in first]
    assert kinds[1] == "addSheet" and first[1]["addSheet"]["properties"]["sheetId"] == sheet.TEMP_ID
    assert first[2] == {"deleteNamedRange": {"namedRangeId": "123"}}
    assert first[3] == {"deleteSheet": {"sheetId": 0}} and first[4] == {"deleteSheet": {"sheetId": 1020}}
    assert first[-1] == {"deleteSheet": {"sheetId": sheet.TEMP_ID}}
    with pytest.raises(ValueError, match="replace_sheet_ids"):
        tools.report_sheet_plan(b["束ID"], run=_detail_run([]), replace_sheet_ids="x")


def test_グラフは全部_系列があり_単価のグラフは正しい列と行を指す():
    """2026-11-21: 日別・週別のコンバージョン単価のグラフが空だった。先頭の日・週の "—" の文字で、
    シートが addChart の系列を落としていた。グラフが読む範囲には "—" を入れない。"""
    _, p, _ = _full_plan()
    later = p["あとで送る要求"]
    sids = {sid: t for t, sid in sheet.IDS.items()}
    charts = [r["addChart"]["chart"] for r in later if "addChart" in r]
    assert len(charts) >= 6
    for c in charts:
        ser = c["spec"]["basicChart"].get("series") or []
        assert ser, c["spec"].get("title")
        for s in ser:
            srcs = s["series"]["sourceRange"]["sources"]
            assert srcs and all(x.get("endRowIndex", 0) > x.get("startRowIndex", 0) and
                                x.get("endColumnIndex", 0) > x.get("startColumnIndex", 0) for x in srcs)
            assert all(x["sheetId"] in sids for x in srcs)
    by = {c["spec"].get("title"): c["spec"]["basicChart"] for c in charts}
    vals = p["書く値"]

    def cells(bc, i=0):
        x = bc["series"][i]["series"]["sourceRange"]["sources"][0]
        g = vals[f"'{sids[x['sheetId']]}'!A1"]
        return x, [[(g[r][c] if r < len(g) and c < len(g[r]) else "") for c in range(x["startColumnIndex"], x["endColumnIndex"])]
                   for r in range(x["startRowIndex"], x["endRowIndex"])]

    # 日別: 日別タブのコンバージョン単価の列（J、index 9）、見出しの行から最後の日まで
    d = by["日別のコンバージョン単価"]
    assert d["chartType"] == "COLUMN" and d["headerCount"] == 1
    x, got = cells(d)
    assert sids[x["sheetId"]] == sheet.DAILY_TAB and x["startColumnIndex"] == 9 and x["endColumnIndex"] == 10
    dom = d["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert (dom["startRowIndex"], dom["endRowIndex"]) == (x["startRowIndex"], x["endRowIndex"]) and dom["startColumnIndex"] == 0
    assert got[0] == ["コンバージョン単価"] and len(got) == 8
    # 週別: 「指標＼週」の行が軸、「コンバージョン単価」の行が系列、A から今週まで
    w = by["週別のコンバージョン単価"]
    assert w["chartType"] == "LINE" and w["headerCount"] == 1
    x, got = cells(w)
    assert sids[x["sheetId"]] == sheet.TREND_TAB and x["endRowIndex"] - x["startRowIndex"] == 1
    tg = vals[f"'{sheet.TREND_TAB}'!A1"]
    assert x["startColumnIndex"] == 0 and x["endColumnIndex"] == len(tg[5])
    assert got[0][0] == "コンバージョン単価"
    dom = w["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert tg[dom["startRowIndex"]][0] == "指標＼週" and dom["endRowIndex"] - dom["startRowIndex"] == 1
    assert (dom["startColumnIndex"], dom["endColumnIndex"]) == (0, len(tg[5]))
    # グラフが読む値の式は "—" の文字を返さない（返すと系列が落ちる）
    for bc in by.values():
        for i in range(len(bc["series"])):
            _, got = cells(bc, i)
            assert not any('"—"' in str(v) for row in got[1:] if len(got) > 1 for v in row), bc
            assert not any('"—"' in str(v) for v in got[0][1:]), bc
