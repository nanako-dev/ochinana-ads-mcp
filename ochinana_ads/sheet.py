"""レポートを Google スプレッドシートに書くための「書く中身」を作る。ads-mcp はシートに書かない（Google の
スプレッドシートの道具で、運用者の Drive のシートに Claude が書く）。ここは値・式・書式の組み立てだけ。

決まり（スプレッドシートの作り方の決まり）:
- 元のデータ（API の日別の行）だけを値で入れ、合計・率・差・内訳は全部 式（SUMIFS など）にする。数字を打ち直さない
- 期間はラベル付きのセル（入力）に置き、式はそこを指す
- 0 で割る所は IF で守り、「—」を出す
- 元のデータには出どころの注記を付け、最後のタブ「データの出どころ」に 1 行残す
- 日別データのタブは、あとで Google 広告スクリプトが毎日行を足す置き場にもなる（列はこのまま）

確かめ方: 書いたあとに get_values で週次のタブの合計を読み、sheet_check で束の数字と照らす。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Optional

from . import report

DATA_TAB = "日別データ"
DECISION_TAB = "判断の記録"
SOURCE_TAB = "データの出どころ"
IDS = {"week": 0, DATA_TAB: 1001, DECISION_TAB: 1002, SOURCE_TAB: 1009}
DATA_HEAD = ["日付", "媒体", "キャンペーン ID", "キャンペーン", "表示回数", "クリック数", "費用（円）", "コンバージョン",
             "コンバージョン値（円）"]
COL = {"date": "A", "media": "B", "id": "C", "name": "D", "impressions": "E", "clicks": "F", "cost": "G",
       "conversions": "H", "value": "I"}

FMT_INT = "#,##0"
FMT_CV = "#,##0.0"
FMT_PCT = "0.00%"
FMT_PT = '+0.00"pt";-0.00"pt";0.00"pt"'
FMT_DIFF = "+#,##0;-#,##0;0"
FMT_DIFF_CV = "+#,##0.0;-#,##0.0;0.0"
FMT_RATE = "+0.0%;-0.0%;0.0%"
FMT_DATE = "yyyy-mm-dd"

INK = {"red": 0, "green": 0, "blue": 0}
SAGE = {"red": 0.961, "green": 0.973, "blue": 0.957}
MUTED = {"red": 0.404, "green": 0.431, "blue": 0.392}
SIGNAL = {"red": 0.098, "green": 0.906, "blue": 0.431}
RULE = {"red": 0.851, "green": 0.851, "blue": 0.851}
INPUT_BLUE = {"red": 0, "green": 0, "blue": 1}


def q(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


def _sumifs(col: str, lo: str, hi: str, extra: str = "") -> str:
    d = q(DATA_TAB)
    return (f"SUMIFS({d}!${col}:${col},{d}!$A:$A,\">=\"&{lo},{d}!$A:$A,\"<=\"&{hi}{extra})")


SUM_COL = {"表示回数": "E", "クリック数": "F", "費用": "G", "コンバージョン": "H", "コンバージョン値": "I"}
RATIO = {"クリック率": ("クリック数", "表示回数"), "クリック単価": ("費用", "クリック数"), "コンバージョン率": ("コンバージョン", "クリック数"),
         "コンバージョン単価": ("費用", "コンバージョン"), "ROAS": ("コンバージョン値", "費用"), "1件あたりの値": ("コンバージョン値", "コンバージョン")}
METRIC_FMT = {"表示回数": (FMT_INT, "NUMBER"), "クリック数": (FMT_INT, "NUMBER"), "費用": (FMT_INT, "NUMBER"),
              "コンバージョン": (FMT_CV, "NUMBER"), "コンバージョン値": (FMT_INT, "NUMBER"), "クリック率": (FMT_PCT, "PERCENT"),
              "クリック単価": (FMT_INT, "NUMBER"), "コンバージョン率": (FMT_PCT, "PERCENT"), "コンバージョン単価": (FMT_INT, "NUMBER"),
              "ROAS": ("0.0%", "PERCENT"), "1件あたりの値": (FMT_INT, "NUMBER")}


def _letter(i: int) -> str:
    return chr(ord("A") + i)


def metric_formulas(n: int, first_col: int, metrics: List[str], lo: str, hi: str, crit: str = "") -> List[str]:
    """1 行ぶんの指標の式（表示・クリック・費用・コンバージョン・値は SUMIFS、率と単価はその行の割り算）。"""
    col = {m: _letter(first_col + i) for i, m in enumerate(metrics)}
    out = []
    for m in metrics:
        if m in SUM_COL:
            out.append("=" + _sumifs(SUM_COL[m], lo, hi, crit))
        else:
            num_m, den_m = RATIO[m]
            out.append(f'=IF({col[den_m]}{n}=0,"—",{col[num_m]}{n}/{col[den_m]}{n})')
    return out


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s.strip()


def md_sections(md: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    cur = None
    for ln in md.splitlines():
        m = re.match(r"##\s+\d+\.\s*(.*)", ln)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur is not None and ln.strip():
            out[cur].append(ln)
    return out


def md_actions(lines: List[str]) -> List[dict]:
    acts, cur = [], None
    for ln in lines:
        m = re.match(r"^(\d+)\.\s+(.*)", ln)
        if m:
            cur = {"順": int(m.group(1)), "何をする": _strip_md(m.group(2)), "なぜ": "", "段": "", "誰の案": "",
                   "承認": "", "いつ・何で評価": ""}
            acts.append(cur)
            continue
        t = ln.strip()
        if cur is None or not t.startswith("- "):
            continue
        t = _strip_md(t[2:])
        if t.startswith("なぜ:"):
            cur["なぜ"] = t[3:].strip()
        elif t.startswith("いつ・何で評価:"):
            cur["いつ・何で評価"] = t.split(":", 1)[1].strip()
        elif t.startswith("段:"):
            for part in t.split("／"):
                k, _, v = part.partition(":")
                k = k.strip()
                if k in ("段", "誰の案", "承認"):
                    cur[k] = v.strip()
    return acts


def plan(bundle: dict, rows: List[dict], md: str = "", tab: str = "", decisions: Optional[List[dict]] = None,
         run_at: str = "") -> dict:
    """シートに書く値（式つき）と、書式・グラフの要求を返す。"""
    cond = bundle["条件"]
    s, e = (date.fromisoformat(x) for x in cond["今の期間"].split("〜"))
    cs, ce = (date.fromisoformat(x) for x in cond["比較期間"].split("〜"))
    tab = tab or f"週次_{e.isoformat()}"
    W = q(tab)
    has_value = any(r["指標"] == "ROAS" for r in bundle["合計"])

    # ---- 日別データ（元の行。値で入れる）
    # 元の行（A〜I）は値。率と単価（J〜）は 1 行目の ARRAYFORMULA で、行が増えても（スクリプトが足しても）自動で計算する
    data_derived = [m for m in report.table_metrics(not has_value) if m in RATIO]
    raw_col = {"表示回数": "E", "クリック数": "F", "費用": "G", "コンバージョン": "H", "コンバージョン値": "I"}
    head_row = list(DATA_HEAD)
    for m in data_derived:
        num_m, den_m = RATIO[m]
        nc, dc = raw_col[num_m], raw_col[den_m]
        head_row.append(f'={{"{m}";ARRAYFORMULA(IF(LEN($A$2:$A)=0,,IF(${dc}$2:${dc}=0,"—",${nc}$2:${nc}/${dc}$2:${dc})))}}')
    data = [head_row]
    for r in sorted(rows, key=lambda x: (x["date"], x["name"])):
        data.append([r["date"], r.get("media") or "Google 広告", "'" + str(r.get("key", "")), r["name"],
                     int(r["impressions"]), int(r["clicks"]), round(float(r["cost"]), 6), round(float(r["conversions"]), 6),
                     round(float(r["value"]), 6)] + [None] * len(data_derived))

    # ---- 週次のタブ
    g: List[List] = []

    def row(*cells):
        g.append(list(cells))
        return len(g)  # 1 始まりの行番号

    row(f"週次の運用レポート（自分用）{bundle.get('対象アカウント', '')}")
    row("数字は「日別データ」から式で計算している。青い字は入力（期間）。")
    row()
    r_s = row("今の期間の初日", cond["今の期間"].split("〜")[0])
    r_e = row("今の期間の最終日", cond["今の期間"].split("〜")[1])
    r_cs = row("比べる期間の初日", cond["比較期間"].split("〜")[0])
    r_ce = row("比べる期間の最終日", cond["比較期間"].split("〜")[1])
    r_conf = row("成果を確定とみなした最後の日", cond["成果を確定とみなした最後の日"])
    row("束ID", bundle.get("束ID", ""))
    row()
    S, E, CS, CE = (f"$B${r_s}", f"$B${r_e}", f"$B${r_cs}", f"$B${r_ce}")
    r_head = row("指標", "今", "前", "差", "増減率")
    base = {}
    for name, col in (("表示回数", "impressions"), ("クリック数", "clicks"), ("費用", "cost"), ("コンバージョン", "conversions"),
                      ("コンバージョン値", "value")):
        if name == "コンバージョン値" and not has_value:
            continue
        n = len(g) + 1
        base[name] = n
        row(name, "=" + _sumifs(COL[col], S, E), "=" + _sumifs(COL[col], CS, CE),
            f"=B{n}-C{n}", f'=IF(C{n}=0,"—",(B{n}-C{n})/C{n})')
    I, C, Y, V = base["表示回数"], base["クリック数"], base["費用"], base["コンバージョン"]
    derived = [("クリック率", C, I, True), ("クリック単価", Y, C, False), ("コンバージョン率", V, C, True),
               ("コンバージョン単価", Y, V, False)]
    if has_value:
        derived += [("ROAS", base["コンバージョン値"], Y, True), ("1件あたりの値", base["コンバージョン値"], V, False)]
    rate_rows, yen_rows = [], []
    for name, num, den, is_rate in derived:
        n = len(g) + 1
        (rate_rows if is_rate else yen_rows).append(n)
        cell = lambda colx: f'=IF({colx}{den}=0,"—",{colx}{num}/{colx}{den})'
        diff = (f'=IF(AND(ISNUMBER(B{n}),ISNUMBER(C{n})),(B{n}-C{n})*100,"—")' if is_rate else
                f'=IF(AND(ISNUMBER(B{n}),ISNUMBER(C{n})),B{n}-C{n},"—")')
        row(name, cell("B"), cell("C"), diff, f'=IF(AND(ISNUMBER(B{n}),ISNUMBER(C{n}),C{n}<>0),(B{n}-C{n})/C{n},"—")')
    r_tot_end = len(g)
    row()

    metrics = report.table_metrics(not has_value)
    nm_ = len(metrics)

    # 日別（グラフの元）。指標は合計と同じ全部（運用者の指定）
    r_dhead = row("日付", *metrics, "成果")
    r_d0 = len(g) + 1
    for i in range(report.period_days(s, e)):
        n = len(g) + 1
        dcell = f"={S}" if i == 0 else f"=A{n - 1}+1"
        row(dcell, *metric_formulas(n, 1, metrics, f"$A{n}", f"$A{n}"), f'=IF(A{n}>$B${r_conf},"未確定","確定")')
    r_d1 = len(g)
    row()

    # キャンペーン別（対象ごとに 今 と 前 の 2 行）
    r_chead = row("キャンペーン", "期間", *metrics, "費用の構成比")
    r_c0 = len(g) + 1
    names = [x["対象"] for x in bundle.get("内訳", [])]
    d = q(DATA_TAB)
    cost_col = _letter(2 + metrics.index("費用"))
    for nm in names:
        for label, lo, hi in (("今", S, E), ("前", CS, CE)):
            n = len(g) + 1
            crit = f",{d}!$D:$D,$A{n}"
            share = f'=IF($B${Y}=0,"—",{cost_col}{n}/$B${Y})' if label == "今" else ""
            row(nm.replace('"', "'"), label, *metric_formulas(n, 2, metrics, lo, hi, crit), share)
    r_c1 = len(g)
    row()

    # 文の欄（Markdown から）
    secs = md_sections(md) if md else {}
    text_rows = []

    def text_block(title, lines):
        if not lines:
            return
        text_rows.append(row(title))
        for ln in lines:
            t = re.sub(r"^\s*-\s*", "", ln)
            if ln.strip().startswith("|"):
                continue
            row(_strip_md(t))
        row()

    for key in ("いちばん大事なこと", "推測（仮説）"):
        text_block(key, secs.get(key, []))
    acts = md_actions(secs.get("ネクストアクション", []))
    r_ahead = None
    if acts:
        text_rows.append(row("ネクストアクション"))
        r_ahead = row("順", "何をする", "なぜ", "段", "誰の案", "承認", "いつ・何で評価")
        for a in acts:
            row(a["順"], a["何をする"], a["なぜ"], a["段"], a["誰の案"], a["承認"], a["いつ・何で評価"])
        row()
    text_block("確かめていないこと・暫定", secs.get("確かめていないこと・暫定", []))
    r_a1 = (r_ahead + len(acts)) if r_ahead else None

    # ---- 判断の記録
    dec = [["日付", "判断", "誰の判断", "対象", "評価の時期", "記録の id"]]
    for x in decisions or []:
        dec.append([x.get("日", ""), x.get("判断", ""), x.get("誰の判断", ""), x.get("対象", ""), x.get("評価の時期", ""),
                    x.get("記録の id", "")])

    # ---- データの出どころ
    src = [["書いた場所", "出どころ", "対象", "取り方", "問い合わせ", "条件", "取った日時", "頼んだ人", "行数", "取ったあとの加工", "注記"],
           [f"{DATA_TAB}!A1:I{len(data)}", "Google 広告 API（読むだけ）", f"{bundle.get('対象アカウント', '')} の campaign（segments.date）",
            "Claude が ads-mcp の report_numbers で取得", "SELECT campaign.id, campaign.name, segments.date, metrics.impressions, "
            "metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign WHERE segments.date BETWEEN …",
            f"今 {cond['今の期間']}／比べる {cond['比較期間']}", run_at or "", "運用者", len(data) - 1,
            "費用は micros を円に直した。並びは日付・キャンペーン名", f"束ID {bundle.get('束ID', '')}"]]

    values = {f"{W}!A1": g, f"{q(DATA_TAB)}!A1": data, f"{q(DECISION_TAB)}!A1": dec, f"{q(SOURCE_TAB)}!A1": src}

    # ---- 書式とグラフ
    wid = IDS["week"]

    def rng(sid, r0, r1, c0, c1):
        return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def fmt(sid, r0, r1, c0, c1, f, fields):
        return {"repeatCell": {"range": rng(sid, r0, r1, c0, c1), "cell": {"userEnteredFormat": f}, "fields": fields}}

    def num(sid, r0, r1, c0, c1, pattern, typ="NUMBER"):
        return fmt(sid, r0, r1, c0, c1, {"numberFormat": {"type": typ, "pattern": pattern}}, "userEnteredFormat.numberFormat")

    def head(sid, r_1based, ncols):
        return fmt(sid, r_1based - 1, r_1based, 0, ncols,
                   {"backgroundColorStyle": {"rgbColor": SAGE}, "textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": MUTED}}},
                   "userEnteredFormat.backgroundColorStyle,userEnteredFormat.textFormat")

    reqs = [
        {"updateSpreadsheetProperties": {"properties": {"timeZone": "Asia/Tokyo"}, "fields": "timeZone"}},
        {"updateSheetProperties": {"properties": {"sheetId": wid, "title": tab, "index": 0}, "fields": "title,index"}},
    ]
    for t, sid, idx in ((DATA_TAB, IDS[DATA_TAB], 1), (DECISION_TAB, IDS[DECISION_TAB], 2), (SOURCE_TAB, IDS[SOURCE_TAB], 3)):
        reqs.append({"addSheet": {"properties": {"sheetId": sid, "title": t, "index": idx}}})
    reqs += [
        fmt(wid, 0, len(g) + 5, 0, nm_ + 4, {"textFormat": {"fontFamily": "Arial"}, "verticalAlignment": "TOP"},
            "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.verticalAlignment"),
        fmt(wid, 0, 1, 0, 1, {"textFormat": {"bold": True, "fontSize": 14}}, "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize"),
        fmt(wid, 1, 2, 0, 1, {"textFormat": {"foregroundColorStyle": {"rgbColor": MUTED}}}, "userEnteredFormat.textFormat.foregroundColorStyle"),
        fmt(wid, r_s - 1, r_conf, 1, 2, {"textFormat": {"foregroundColorStyle": {"rgbColor": INPUT_BLUE}},
                                         "numberFormat": {"type": "DATE", "pattern": FMT_DATE}, "horizontalAlignment": "LEFT"},
            "userEnteredFormat.textFormat.foregroundColorStyle,userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"),
        fmt(wid, r_cs - 1, r_ce, 1, 2, {"textFormat": {"foregroundColorStyle": {"rgbColor": INK}}},
            "userEnteredFormat.textFormat.foregroundColorStyle"),
        head(wid, r_head, 5), head(wid, r_dhead, nm_ + 2), head(wid, r_chead, nm_ + 3),
    ]
    # 比べる期間は入力から式で出す（直前の同じ日数のとき）
    if (cs, ce) == (s - timedelta(days=(e - s).days + 1), s - timedelta(days=1)):
        g[r_cs - 1][1] = f"=B{r_s}-(B{r_e}-B{r_s}+1)"
        g[r_ce - 1][1] = f"=B{r_s}-1"
    for name, n in base.items():
        pat = FMT_CV if name == "コンバージョン" else FMT_INT
        reqs.append(num(wid, n - 1, n, 1, 3, pat))
        reqs.append(num(wid, n - 1, n, 3, 4, FMT_DIFF_CV if name == "コンバージョン" else FMT_DIFF))
    for n in rate_rows:
        reqs += [num(wid, n - 1, n, 1, 3, FMT_PCT, "PERCENT"), num(wid, n - 1, n, 3, 4, FMT_PT)]
    for n in yen_rows:
        reqs += [num(wid, n - 1, n, 1, 3, FMT_INT), num(wid, n - 1, n, 3, 4, FMT_DIFF)]
    reqs.append(num(wid, r_head, r_tot_end, 4, 5, FMT_RATE, "PERCENT"))
    reqs += [fmt(wid, r_head, r_tot_end, 1, 5, {"horizontalAlignment": "RIGHT"}, "userEnteredFormat.horizontalAlignment"),
             {"updateBorders": {"range": rng(wid, r_head - 1, r_tot_end, 0, 5), "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": INK}},
                                "top": {"style": "SOLID", "colorStyle": {"rgbColor": INK}},
                                "innerHorizontal": {"style": "DASHED", "colorStyle": {"rgbColor": RULE}}}}]
    reqs.append(num(wid, r_d0 - 1, r_d1, 0, 1, FMT_DATE, "DATE"))
    reqs.append(fmt(wid, r_d0 - 1, r_c1, 1, nm_ + 3, {"horizontalAlignment": "RIGHT"}, "userEnteredFormat.horizontalAlignment"))
    if names:
        reqs.append(fmt(wid, r_c0 - 1, r_c1, 1, 2, {"horizontalAlignment": "CENTER"}, "userEnteredFormat.horizontalAlignment"))
    for i, m in enumerate(metrics):
        pat, typ = METRIC_FMT[m]
        reqs.append(num(wid, r_d0 - 1, r_d1, 1 + i, 2 + i, pat, typ))
        if names:
            reqs.append(num(wid, r_c0 - 1, r_c1, 2 + i, 3 + i, pat, typ))
    if names:
        reqs.append(num(wid, r_c0 - 1, r_c1, 2 + nm_, 3 + nm_, "0.0%", "PERCENT"))
    for n in text_rows:
        reqs.append(fmt(wid, n - 1, n, 0, 1, {"textFormat": {"bold": True, "fontSize": 12}},
                        "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize"))
    if r_ahead:
        reqs += [head(wid, r_ahead, 7),
                 fmt(wid, r_ahead, r_a1, 0, 7, {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy")]
    reqs += [
        {"updateDimensionProperties": {"range": {"sheetId": wid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": 300}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": wid, "dimension": "COLUMNS", "startIndex": 1, "endIndex": nm_ + 3},
                                       "properties": {"pixelSize": 120}, "fields": "pixelSize"}},
        {"updateSheetProperties": {"properties": {"sheetId": wid, "gridProperties": {"hideGridlines": True}},
                                   "fields": "gridProperties.hideGridlines"}},
        # グラフ: 日別の費用（棒）とコンバージョン（線、右の目盛り）
        {"addChart": {"chart": {"spec": {"title": "日別の費用とコンバージョン", "basicChart": {
            "chartType": "COMBO", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
            "axis": [{"position": "LEFT_AXIS", "title": "費用（円）"}, {"position": "RIGHT_AXIS", "title": "コンバージョン"}],
            "domains": [{"domain": {"sourceRange": {"sources": [rng(wid, r_dhead - 1, r_d1, 0, 1)]}}}],
            "series": [{"series": {"sourceRange": {"sources": [rng(wid, r_dhead - 1, r_d1, 1 + metrics.index("費用"), 2 + metrics.index("費用"))]}}, "targetAxis": "LEFT_AXIS",
                        "type": "COLUMN", "colorStyle": {"rgbColor": INK}},
                       {"series": {"sourceRange": {"sources": [rng(wid, r_dhead - 1, r_d1, 1 + metrics.index("コンバージョン"), 2 + metrics.index("コンバージョン"))]}}, "targetAxis": "RIGHT_AXIS",
                        "type": "LINE", "colorStyle": {"rgbColor": SIGNAL}}]}},
            "position": {"overlayPosition": {"anchorCell": {"sheetId": wid, "rowIndex": 2, "columnIndex": 5},
                                             "widthPixels": 620, "heightPixels": 300}}}}},
    ]
    # 日別データ・判断の記録・出どころ
    did = IDS[DATA_TAB]
    for i, m in enumerate(data_derived):
        pat, typ = METRIC_FMT[m]
        c = len(DATA_HEAD) + i
        reqs.append({"repeatCell": {"range": {"sheetId": did, "startRowIndex": 1, "startColumnIndex": c, "endColumnIndex": c + 1},
                                    "cell": {"userEnteredFormat": {"numberFormat": {"type": typ, "pattern": pat},
                                                                   "horizontalAlignment": "RIGHT"}},
                                    "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"}})
    reqs += [head(did, 1, len(DATA_HEAD) + len(data_derived)),
             {"updateSheetProperties": {"properties": {"sheetId": did, "gridProperties": {"frozenRowCount": 1}},
                                        "fields": "gridProperties.frozenRowCount"}},
             num(did, 1, max(len(data), 2), 0, 1, FMT_DATE, "DATE"),
             num(did, 1, max(len(data), 2), 4, 7, FMT_INT), num(did, 1, max(len(data), 2), 7, 8, FMT_CV),
             num(did, 1, max(len(data), 2), 8, 9, FMT_INT),
             {"updateCells": {"rows": [{"values": [{"note": (
                 f"Source: Google 広告 API（{bundle.get('対象アカウント', '')}、campaign の日別）、{len(data) - 1} 行、"
                 f"取得 {run_at}。Claude が ads-mcp で取得（読むだけ）。全文はタブ「{SOURCE_TAB}」")}]}],
                 "fields": "note", "start": {"sheetId": did, "rowIndex": 0, "columnIndex": 0}}},
             {"updateDimensionProperties": {"range": {"sheetId": did, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4},
                                            "properties": {"pixelSize": 280}, "fields": "pixelSize"}},
             head(IDS[DECISION_TAB], 1, 6), head(IDS[SOURCE_TAB], 1, 11),
             fmt(IDS[SOURCE_TAB], 1, 2, 0, 11, {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
                 "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment")]
    expect = {"タブ": tab, "合計の範囲": f"{W}!A{r_head}:E{r_tot_end}"}
    return {"先に送る要求": reqs[:5], "書く値": values, "あとで送る要求": reqs[5:], "確かめる範囲": expect,
            "書き方": ["1. update_spreadsheet で「先に送る要求」（時間帯・タブの名前・タブの追加）を送る。タブが無いと値を書けない",
                     "2. 「書く値」を範囲ごとに update_formulas で書く（式は = で始まる。日付は文字のまま渡せば日付になる）",
                     "3. 「あとで送る要求」（書式・グラフ）を update_spreadsheet で送る",
                     "4. get_values で「確かめる範囲」を読み、report_sheet_check(束ID, 読んだ値) で束と照らす"]}


def parse_cell(s: str):
    t = str(s).strip().replace(",", "").replace("pt", "")
    if t in ("", "—"):
        return None
    pct = t.endswith("%")
    t = t.rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return s
    return (v, "%") if pct else (v, "")


def _same(name: str, cell, exp) -> bool:
    got = parse_cell(cell)
    if got is None or exp is None:
        return got is None and exp is None
    if not isinstance(got, tuple):
        return False
    d = report.DIGITS[name]
    shown = len(str(cell).replace("%", "").split(".")[1]) if "." in str(cell) else 0
    # シートの表示の桁が束と同じなら完全に一致。表示の桁が多いときだけ、二度の丸めの差（1 桁）を許す
    tol = 1e-9 if shown <= d else 10 ** -d + 1e-9
    return abs(report.hround(got[0], d) - float(exp)) <= tol


def check(bundle: dict, rows: List[List[str]]) -> dict:
    """シート（get_values の表示の文字）を束と照らす。合計の表・日別の表・キャンペーン別の表を見出しの行で見分ける。"""
    want = {r["指標"]: r for r in bundle["合計"]}
    days = {d["日付"]: d for d in bundle.get("日別", [])}
    camps = {r["対象"]: r for r in bundle.get("内訳", [])}
    out, bad = [], []
    mode, head = None, []
    seen_tot, seen_day, seen_camp = set(), set(), set()

    def put(where, name, cell, exp):
        ok = _same(name, cell, exp)
        item = {"表": where, "指標": name, "シート": cell, "束": exp, "一致": ok}
        out.append(item)
        if not ok:
            bad.append(item)

    for r in rows:
        if not r:
            mode = None
            continue
        if r[0] == "指標":
            mode, head = "合計", r
            continue
        if r[0] == "日付":
            mode, head = "日別", r
            continue
        if r[0] == "キャンペーン":
            mode, head = "キャンペーン別", r
            continue
        if mode == "合計" and r[0] in want:
            seen_tot.add(r[0])
            for i, key in ((1, "今"), (2, "前")):
                put(f"合計（{key}）", r[0], r[i] if i < len(r) else "", want[r[0]][key])
        elif mode == "日別" and r[0] in days:
            seen_day.add(r[0])
            for i, h in enumerate(head[1:], 1):
                if h in report.DIGITS:
                    put(f"日別 {r[0]}", h, r[i] if i < len(r) else "", days[r[0]].get(h))
        elif mode == "キャンペーン別" and r[0] in camps and len(r) > 1 and r[1] in ("今", "前"):
            seen_camp.add(r[0])
            for i, h in enumerate(head[2:], 2):
                if h in report.DIGITS:
                    put(f"{r[0]}（{r[1]}）", h, r[i] if i < len(r) else "", camps[r[0]].get(f"{h}（{r[1]}）"))
    missing = [f"合計の{n}" for n in want if n not in seen_tot]
    if any(m == "日別" for m in [x["表"][:2] for x in out]) or seen_day:
        missing += [f"日別 {d}" for d in days if d not in seen_day]
    if seen_camp:
        missing += [f"キャンペーン {c}" for c in camps if c not in seen_camp]
    return {"結果": "全部一致" if not bad and not missing else f"{len(bad)} 件が合わない・{len(missing)} 件が無い",
            "合わない": bad, "シートに無いもの": missing, "照らした数": len(out)}
