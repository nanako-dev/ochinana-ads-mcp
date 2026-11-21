"""レポートを Google スプレッドシートに書くための「書く中身」を作る。ads-mcp はシートに書かない（Google の
スプレッドシートの道具で、運用者の Drive のシートに Claude が書く）。ここは値・式・書式の組み立てだけ。

決まり（スプレッドシートの作り方の決まり）:
- 元のデータ（API の日別の行）だけを値で入れ、合計・率・差・内訳は全部 式（SUMIFS など）にする。数字を打ち直さない
- 期間は「設定」のタブの青い字のセル（入力）に置き、ほかのタブの式は全部そこを指す
- 0 で割る所は IF で守り、「—」を出す
- 元のデータには出どころの注記を付け、最後のタブ「データの出どころ」に 1 行残す
- 日別データのタブは、あとで Google 広告スクリプトが毎日行を足す置き場にもなる（列はこのまま）

形（版 0.16.0。運用者の見本: 広い順に絞る・表とグラフ・比べる所は数字のすぐ横で目立たせる）:
  サマリー → 週別の推移 → 日別 → キャンペーン別 → 広告グループ別 → 広告別 → キーワード別 → 検索語句別
  → 判断の記録 → 設定 → 日別データ → 日別データ_詳細 → データの出どころ
階層のタブは 1 対象 1 行で、今週｜前週｜前週比 の列の塊を横に並べる（縦に今と前を積まない）。

確かめ方: 書いたあとに get_values で「確かめる範囲」を読み、check で束と保存した行に照らす。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from . import report

SUMMARY_TAB = "サマリー"
TREND_TAB = "週別の推移"
DAILY_TAB = "日別"
SETTINGS_TAB = "設定"
DATA_TAB = "日別データ"
DETAIL_TAB = "日別データ_詳細"
DECISION_TAB = "判断の記録"
SOURCE_TAB = "データの出どころ"
LEVEL_TABS = [("キャンペーン", "キャンペーン別", 2010), ("広告グループ", "広告グループ別", 2011), ("広告", "広告別", 2012),
              ("キーワード", "キーワード別", 2013), ("検索語句", "検索語句別", 2014)]
IDS = {SUMMARY_TAB: 2000, TREND_TAB: 2001, DAILY_TAB: 2002, DECISION_TAB: 2020, SETTINGS_TAB: 2021, DATA_TAB: 2030,
       DETAIL_TAB: 2031, SOURCE_TAB: 2032}
TEMP_ID = 2999            # 作り直しのときだけ使う仮のタブ（シートは最後の 1 枚を消せないので）
TAB_ORDER = ([SUMMARY_TAB, TREND_TAB, DAILY_TAB] + [t for _, t, _ in LEVEL_TABS]
             + [DECISION_TAB, SETTINGS_TAB, DATA_TAB, DETAIL_TAB, SOURCE_TAB])
DATA_TABS = (DATA_TAB, DETAIL_TAB, SOURCE_TAB)
MAX_ENTITIES = 30         # 1 つのタブに並べる対象の上限（今週の費用の大きい順。残りは日別データ_詳細に残る）
WEEKS = 8                 # 週別の推移の週の数
VALUE_MIN_ROAS = 50.0     # コンバージョン値の列（値・ROAS・1件あたりの値）は、ROAS がこれ以上（%）のときだけ表に出す
FONT = "Noto Sans JP"

DETAIL_HEAD = ["日付", "媒体", "階層", "キャンペーン", "広告グループ", "対象 ID", "対象", "種類", "表示回数", "クリック数",
               "費用（円）", "コンバージョン", "コンバージョン値（円）"]
DETAIL_SUM = {"表示回数": "I", "クリック数": "J", "費用": "K", "コンバージョン": "L", "コンバージョン値": "M"}
# 日別データ_詳細 の列に名前を付けて、階層のタブの式を短くする（式が長いとシートに書く量が増える）
DETAIL_NAMES = {"A": "D_DATE", "C": "D_LEVEL", "F": "D_ID", "I": "D_IMP", "J": "D_CLK", "K": "D_COST", "L": "D_CV", "M": "D_VAL"}
D_NAME = {"表示回数": "D_IMP", "クリック数": "D_CLK", "費用": "D_COST", "コンバージョン": "D_CV", "コンバージョン値": "D_VAL"}
DATA_HEAD = ["日付", "媒体", "キャンペーン ID", "キャンペーン", "表示回数", "クリック数", "費用（円）", "コンバージョン",
             "コンバージョン値（円）"]
SUM_COL = {"表示回数": "E", "クリック数": "F", "費用": "G", "コンバージョン": "H", "コンバージョン値": "I"}
RATIO = {"クリック率": ("クリック数", "表示回数"), "クリック単価": ("費用", "クリック数"), "コンバージョン率": ("コンバージョン", "クリック数"),
         "コンバージョン単価": ("費用", "コンバージョン"), "ROAS": ("コンバージョン値", "費用"), "1件あたりの値": ("コンバージョン値", "コンバージョン")}
IDENT = {
    "キャンペーン": [("キャンペーン", "campaign")],
    "広告グループ": [("キャンペーン", "campaign"), ("広告グループ", "ad_group")],
    "広告": [("キャンペーン", "campaign"), ("広告グループ", "ad_group"), ("広告", "name"), ("種類", "sub")],
    "キーワード": [("キャンペーン", "campaign"), ("広告グループ", "ad_group"), ("キーワード", "name"), ("一致方式", "sub")],
    "検索語句": [("キャンペーン", "campaign"), ("広告グループ", "ad_group"), ("検索語句", "name")],
}
GOOD_UP = ("表示回数", "クリック数", "クリック率", "コンバージョン", "コンバージョン率", "コンバージョン値", "ROAS", "1件あたりの値")
BAD_UP = ("クリック単価", "コンバージョン単価")   # 上がると悪い。費用はどちらでもない（色を付けない）

FMT_INT = "#,##0"
FMT_CV = "#,##0.0"
FMT_PCT = "0.00%"
FMT_PT = '+0.00"pt";-0.00"pt";0.00"pt"'
FMT_DIFF = "+#,##0;-#,##0;0"
FMT_DIFF_CV = "+#,##0.0;-#,##0.0;0.0"
FMT_RATE = "+0.0%;-0.0%;0.0%"
FMT_ISO = "yyyy-mm-dd"
FMT_MD = "m/d"
METRIC_FMT = {"表示回数": (FMT_INT, "NUMBER"), "クリック数": (FMT_INT, "NUMBER"), "費用": (FMT_INT, "NUMBER"),
              "コンバージョン": (FMT_CV, "NUMBER"), "コンバージョン値": (FMT_INT, "NUMBER"), "クリック率": (FMT_PCT, "PERCENT"),
              "クリック単価": (FMT_INT, "NUMBER"), "コンバージョン率": (FMT_PCT, "PERCENT"), "コンバージョン単価": (FMT_INT, "NUMBER"),
              "ROAS": ("0.0%", "PERCENT"), "1件あたりの値": (FMT_INT, "NUMBER")}
CARD_FMT = {"表示回数": '#,##0" 回"', "クリック数": '#,##0" 回"', "費用": '#,##0" 円"', "コンバージョン": '#,##0.0" 件"',
            "クリック単価": '#,##0" 円"', "コンバージョン単価": '#,##0" 円"', "クリック率": FMT_PCT, "コンバージョン率": FMT_PCT}
CARD_UNIT = {"表示回数": ("#,##0", " 回"), "クリック数": ("#,##0", " 回"), "費用": ("#,##0", " 円"), "コンバージョン": ("#,##0.0", " 件"),
             "クリック単価": ("#,##0", " 円"), "コンバージョン単価": ("#,##0", " 円")}
CARDS_1 = ["費用", "コンバージョン", "コンバージョン単価", "コンバージョン率"]
CARDS_2 = ["表示回数", "クリック数", "クリック率", "クリック単価"]

INK = {"red": 0.13, "green": 0.13, "blue": 0.13}
NEAR_BLACK = {"red": 0.15, "green": 0.15, "blue": 0.15}
SAGE = {"red": 0.933, "green": 0.953, "blue": 0.925}
MUTED = {"red": 0.45, "green": 0.47, "blue": 0.44}
RULE = {"red": 0.851, "green": 0.851, "blue": 0.851}
INPUT_BLUE = {"red": 0, "green": 0, "blue": 1}
WHITE = {"red": 1, "green": 1, "blue": 1}
PALE = {"red": 0.976, "green": 0.976, "blue": 0.976}
GOOD = {"red": 0.788, "green": 0.969, "blue": 0.863}   # 良い向きの色（淡い緑）
BAD = {"red": 1.0, "green": 0.8, "blue": 0.8}          # 悪い向きの色（淡い赤）
GREEN_TXT = {"red": 0.05, "green": 0.5, "blue": 0.25}
RED_TXT = {"red": 0.8, "green": 0.1, "blue": 0.1}
DARK_GREEN = {"red": 0.106, "green": 0.369, "blue": 0.208}
PREV_GREY = {"red": 0.72, "green": 0.72, "blue": 0.72}
COST_GREY = {"red": 0.78, "green": 0.78, "blue": 0.78}
LINE_GREEN = {"red": 0.13, "green": 0.6, "blue": 0.33}
TEAL = {"red": 0.2, "green": 0.6, "blue": 0.55}
TAB_GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}


def q(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


SET = q(SETTINGS_TAB)
S, E, PS, PE, CONF, W0 = (f"{SET}!$B${n}" for n in (4, 5, 6, 7, 8, 9))


def col(i: int) -> str:
    """0 始まりの列の番号を A・B・…・Z・AA の形に。"""
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s.strip()


def _plain_line(s: str) -> str:
    """箇条書きの印と【事実】のような札を外す。"""
    s = re.sub(r"^\s*[-*]\s*", "", s)
    s = re.sub(r"【[^】]{1,8}】\s*", "", s)
    return _strip_md(s)


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


def account_name(label: str) -> str:
    m = re.search(r"（(.+)）", label or "")
    return m.group(1) if m else (label or "")


def show_value(bundle: dict) -> bool:
    """コンバージョン値の列を出すか。値が付いていても ROAS が小さい（意味の薄い値）なら、読みやすさを優先して出さない。"""
    r = next((x for x in bundle.get("合計", []) if x["指標"] == "ROAS"), None)
    if not r:
        return False
    vals = [v for v in (r.get("今"), r.get("前")) if isinstance(v, (int, float))]
    return bool(vals) and max(vals) >= VALUE_MIN_ROAS


def _spans(cond: dict) -> List[List[str]]:
    return [cond["今の期間"].split("〜"), cond["比較期間"].split("〜")]


def campaign_details(rows: List[dict], cond: dict) -> List[dict]:
    """キャンペーンの日別の行を、日別データ_詳細 の形（階層「キャンペーン」、対象 ID はキャンペーン ID）に。"""
    out = []
    for r in rows:
        if not any(lo <= r["date"] <= hi for lo, hi in _spans(cond)):
            continue
        out.append({"date": r["date"], "media": r.get("media") or "Google 広告", "level": "キャンペーン",
                    "campaign": r["name"], "ad_group": "", "key": str(r.get("key", "") or r["name"]), "name": r["name"],
                    "sub": "", "impressions": r["impressions"], "clicks": r["clicks"], "cost": r["cost"],
                    "conversions": r["conversions"], "value": r.get("value", 0)})
    return out


def merge_rows(base: List[dict], trend: Optional[List[dict]], cond: dict) -> List[dict]:
    """束の元の行（今週と前週）に、週別の推移のために読んだ行を足す。束の期間の日は束の行を使う（数字を束とそろえる）。"""
    if not trend:
        return list(base)
    keep = [t for t in trend if not any(lo <= t["date"] <= hi for lo, hi in _spans(cond))]
    return list(base) + keep


# ---------------- 書式の要求を作る小さな部品 ----------------

def rng(sid, r0=None, r1=None, c0=None, c1=None) -> dict:
    d = {"sheetId": sid}
    for k, v in (("startRowIndex", r0), ("endRowIndex", r1), ("startColumnIndex", c0), ("endColumnIndex", c1)):
        if v is not None:
            d[k] = v
    return d


def fmt(sid, r0, r1, c0, c1, **o) -> dict:
    """o: bold・italic・size・font・color（字の色）・bg（地の色）・num=(形, 種類)・align・valign・wrap。"""
    uf, fields, tf = {}, [], {}
    for k, key in (("bold", "bold"), ("italic", "italic"), ("size", "fontSize"), ("font", "fontFamily")):
        if k in o:
            tf[key] = o[k]
            fields.append(f"userEnteredFormat.textFormat.{key}")
    if "color" in o:
        tf["foregroundColorStyle"] = {"rgbColor": o["color"]}
        fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
    if tf:
        uf["textFormat"] = tf
    if "bg" in o:
        uf["backgroundColorStyle"] = {"rgbColor": o["bg"]}
        fields.append("userEnteredFormat.backgroundColorStyle")
    if "num" in o:
        p, t = o["num"]
        uf["numberFormat"] = {"type": t, "pattern": p}
        fields.append("userEnteredFormat.numberFormat")
    for k, key in (("align", "horizontalAlignment"), ("valign", "verticalAlignment"), ("wrap", "wrapStrategy")):
        if k in o:
            uf[key] = o[k]
            fields.append(f"userEnteredFormat.{key}")
    return {"repeatCell": {"range": rng(sid, r0, r1, c0, c1), "cell": {"userEnteredFormat": uf}, "fields": ",".join(fields)}}


def merge(sid, r0, r1, c0, c1) -> dict:
    return {"mergeCells": {"range": rng(sid, r0, r1, c0, c1), "mergeType": "MERGE_ALL"}}


def frame(sid, r0, r1, c0, c1, color, style="SOLID_MEDIUM") -> dict:
    b = {"style": style, "colorStyle": {"rgbColor": color}}
    return {"updateBorders": {"range": rng(sid, r0, r1, c0, c1), "top": b, "bottom": b, "left": b, "right": b}}


def width(sid, c0, c1, px) -> dict:
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
                                          "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def height(sid, r0, r1, px) -> dict:
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r0, "endIndex": r1},
                                          "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def rule(sid, r0, r1, c0, c1, formula, **f) -> dict:
    form = {}
    if "color" in f or "bold" in f or "italic" in f:
        tf = {}
        if "color" in f:
            tf["foregroundColorStyle"] = {"rgbColor": f["color"]}
        for k in ("bold", "italic"):
            if k in f:
                tf[k] = f[k]
        form["textFormat"] = tf
    if "bg" in f:
        form["backgroundColorStyle"] = {"rgbColor": f["bg"]}
    return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [rng(sid, r0, r1, c0, c1)], "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": form}}}}


def scale(sid, r0, r1, c, low_good) -> dict:
    lo_c, hi_c = (GOOD, BAD) if low_good else (WHITE, GOOD)
    return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [rng(sid, r0, r1, c, c + 1)], "gradientRule": {
        "minpoint": {"type": "MIN", "colorStyle": {"rgbColor": lo_c}},
        "midpoint": {"type": "PERCENTILE", "value": "50", "colorStyle": {"rgbColor": WHITE}},
        "maxpoint": {"type": "MAX", "colorStyle": {"rgbColor": hi_c}}}}}}


COLOR = {"クリック率": False, "コンバージョン率": False, "クリック単価": True, "コンバージョン単価": True, "ROAS": False}


def color_table(sid, r0, r1, first_col, cols) -> List[dict]:
    return [scale(sid, r0, r1, first_col + i, COLOR[m]) for i, m in enumerate(cols) if m in COLOR] if r1 > r0 else []


def direction_rules(sid, r0, r1, c0, c1, metric, ref) -> List[dict]:
    """比べる数字の色。ref はその範囲の左上のセル（相対）。良い向きは緑、悪い向きは赤。費用は色を付けない。"""
    if metric in GOOD_UP:
        up, down = GREEN_TXT, RED_TXT
    elif metric in BAD_UP:
        up, down = RED_TXT, GREEN_TXT
    else:
        return []
    return [rule(sid, r0, r1, c0, c1, f"=AND(ISNUMBER({ref}),{ref}>0)", color=up, bold=True),
            rule(sid, r0, r1, c0, c1, f"=AND(ISNUMBER({ref}),{ref}<0)", color=down, bold=True)]


def series(sid, r0, r1, c0, c1, axis, typ=None, color=None, label=False) -> dict:
    s = {"series": {"sourceRange": {"sources": [rng(sid, r0, r1, c0, c1)]}}, "targetAxis": axis}
    if typ:
        s["type"] = typ
    if color:
        s["colorStyle"] = {"rgbColor": color}
    if label:
        s["dataLabel"] = {"type": "DATA"}
    return s


def chart(on_sid, title, kind, domain, ser, row, col_, w, h, axes=None, offset_x=0, legend="BOTTOM_LEGEND") -> dict:
    spec = {"chartType": kind, "legendPosition": legend, "headerCount": 1,
            "domains": [{"domain": {"sourceRange": {"sources": [domain]}}}], "series": ser}
    if axes:
        spec["axis"] = axes
    head = {"title": title} if title else {}
    return {"addChart": {"chart": {"spec": {**head, "fontName": FONT, "basicChart": spec},
                                   "position": {"overlayPosition": {"anchorCell": {"sheetId": on_sid, "rowIndex": row, "columnIndex": col_},
                                                                    **({"offsetXPixels": offset_x} if offset_x else {}),
                                                                    "widthPixels": w, "heightPixels": h}}}}}


class Grid:
    """1 始まりの行・0 始まりの列でセルを置き、書く値（行の並び）にする。"""

    def __init__(self):
        self.cells: Dict[tuple, object] = {}

    def set(self, r: int, c: int, v):
        self.cells[(r, c)] = v

    def row(self, r: int, c: int, vals: Sequence):
        for i, v in enumerate(vals):
            if v is not None:
                self.set(r, c + i, v)

    def rows(self) -> List[List]:
        if not self.cells:
            return []
        nr = max(r for r, _ in self.cells)
        out = []
        for r in range(1, nr + 1):
            cs = [c for (rr, c) in self.cells if rr == r]
            out.append([self.cells.get((r, c)) for c in range(max(cs) + 1)] if cs else [])
        return out


def _data_sumifs(metric: str, lo: str, hi: str) -> str:
    d, c = q(DATA_TAB), SUM_COL[metric]
    return f'SUMIFS({d}!${c}:${c},{d}!$A:$A,">="&{lo},{d}!$A:$A,"<="&{hi})'


# グラフが読む範囲で使う「計算できない」の値。空のまま（IF の第 2 引数を省くと空になる）。
# "—" の文字を入れると、Google スプレッドシートは addChart のときにその列・行を数字でないとみなし、
# 系列を黙って落とす（2026-11-21、日別・週別のコンバージョン単価のグラフが空になった。先頭の日・週が "—" だった）。
CHART_NONE = ""


def row_metrics(n: int, first: int, metrics: List[str], sum_expr, none: str = '"—"') -> List[str]:
    """1 行ぶんの指標の式（表示・クリック・費用・コンバージョン・値は sum_expr、率と単価はその行の割り算）。

    none: 分母が 0 のときの値。グラフが読む行は CHART_NONE（空）にする。"""
    c = {m: col(first + i) for i, m in enumerate(metrics)}
    out = []
    for m in metrics:
        if m in RATIO:
            num_m, den_m = RATIO[m]
            out.append(f'=IF({c[den_m]}{n}=0,{none},{c[num_m]}{n}/{c[den_m]}{n})')
        else:
            out.append("=" + sum_expr(m))
    return out


def _dow(ref: str) -> str:
    return f'"（"&MID("日月火水木金土",WEEKDAY({ref}),1)&"）"'


def _add_sheet(sid, title, rows=1000, cols=26, frozen_rows=0, frozen_cols=0, grid=True, tab_color=None) -> dict:
    gp = {"rowCount": rows, "columnCount": cols}
    if frozen_rows:
        gp["frozenRowCount"] = frozen_rows
    if frozen_cols:
        gp["frozenColumnCount"] = frozen_cols
    if not grid:
        gp["hideGridlines"] = True
    p = {"sheetId": sid, "title": title, "gridProperties": gp}
    if tab_color:
        p["tabColorStyle"] = {"rgbColor": tab_color}
    return {"addSheet": {"properties": p}}


# ---------------- 書く中身を作る ----------------

def plan(bundle: dict, rows: List[dict], md: str = "", decisions: Optional[List[dict]] = None, run_at: str = "",
         details: Optional[List[dict]] = None, trend: Optional[List[dict]] = None, weeks: int = WEEKS,
         replace_sheets: Sequence[int] = (), replace_named: Sequence[str] = ()) -> dict:
    """シートに書く値（式つき）と、書式・グラフの要求を返す。

    rows: 束の元の行（今週と前週、キャンペーンの日別）。details: 広告グループ・広告・キーワード・検索語句の日別の行（無ければ
    その 4 つのタブは作らない）。trend: 週別の推移のためのキャンペーンの日別の行（無ければ週別の推移のタブは作らない）。
    replace_sheets・replace_named: 作り直すときに、先に消すタブの sheetId と名前付き範囲の id。
    """
    cond = bundle["条件"]
    s, e = (date.fromisoformat(x) for x in cond["今の期間"].split("〜"))
    cs, ce = (date.fromisoformat(x) for x in cond["比較期間"].split("〜"))
    ndays = report.period_days(s, e)
    prev_is_prev = (cs, ce) == (s - timedelta(days=ndays), s - timedelta(days=1))
    weekly = ndays == 7 and prev_is_prev
    LC, LP = ("今週", "前週") if weekly else ("今の期間", "比べる期間")
    LPW = "前の週" if weekly else "比べる期間"
    acct = account_name(bundle.get("対象アカウント", ""))
    metrics = report.table_metrics(not show_value(bundle))
    nm = len(metrics)
    has_trend = bool(trend) and weeks > 0
    all_rows = merge_rows(rows, trend if has_trend else None, cond)
    w_start = e - timedelta(days=7 * weeks - 1) if has_trend else cs
    values: Dict[str, List[List]] = {}
    later: List[dict] = []
    adds: List[dict] = []
    expect: Dict[str, object] = {"確かめる範囲": [], "階層ごとのタブ": []}

    def check_range(tab, a1):
        expect["確かめる範囲"].append({"タブ": tab, "範囲": f"{q(tab)}!{a1}"})

    # ================= 設定 =================
    st = Grid()
    st.row(1, 0, ["設定（期間と出どころ）"])
    st.row(2, 0, ["青い字は入力です。ほかのタブの数字は、ここの日付を使って計算します。"])
    st.row(4, 0, [f"{LC}の初日", s.isoformat()])
    st.row(5, 0, [f"{LC}の最終日", e.isoformat()])
    st.row(6, 0, [f"{LP}の初日", "=B4-(B5-B4+1)" if prev_is_prev else cs.isoformat()])
    st.row(7, 0, [f"{LP}の最終日", "=B4-1" if prev_is_prev else ce.isoformat()])
    st.row(8, 0, ["成果を確定とみなした最後の日", cond["成果を確定とみなした最後の日"]])
    st.row(9, 0, ["週別の推移の最初の日", f"=B5-{7 * weeks - 1}"])
    st.row(11, 0, ["束の番号（ads-mcp の記録の番号）", bundle.get("束ID", "")])
    st.row(12, 0, ["数字の出どころ", f"Google 広告 API（読むだけ）。くわしくはタブ「{SOURCE_TAB}」にあります。"])
    st.row(13, 0, ["作った日時", run_at or ""])
    values[f"{SET}!A1"] = st.rows()
    sid = IDS[SETTINGS_TAB]
    adds.append(_add_sheet(sid, SETTINGS_TAB, rows=40, cols=4, grid=False))
    blue = [3, 4, 7] + ([] if prev_is_prev else [5, 6])
    later += [fmt(sid, None, None, None, None, font=FONT, valign="MIDDLE"),
              fmt(sid, 0, 1, 0, 1, bold=True, size=14), fmt(sid, 1, 2, 0, 1, color=MUTED),
              fmt(sid, 3, 9, 1, 2, num=(FMT_ISO, "DATE"), align="LEFT"),
              fmt(sid, 3, 13, 0, 1, bold=True), width(sid, 0, 1, 260), width(sid, 1, 2, 360)]
    later += [fmt(sid, r, r + 1, 1, 2, color=INPUT_BLUE) for r in blue]

    # ================= 日別データ（元の行。値で入れる） =================
    derived = [m for m in metrics if m in RATIO]
    head_row = list(DATA_HEAD)
    for m in derived:
        nc, dc = SUM_COL[RATIO[m][0]], SUM_COL[RATIO[m][1]]
        head_row.append(f'={{"{m}";ARRAYFORMULA(IF(LEN($A$2:$A)=0,,IF(${dc}$2:${dc}=0,"—",${nc}$2:${nc}/${dc}$2:${dc})))}}')
    data = [head_row]
    for r in sorted(all_rows, key=lambda x: (x["date"], x["name"])):
        data.append([r["date"], r.get("media") or "Google 広告", "'" + str(r.get("key", "")), r["name"],
                     int(r["impressions"]), int(r["clicks"]), round(float(r["cost"]), 6), round(float(r["conversions"]), 6),
                     round(float(r.get("value") or 0), 6)] + [None] * len(derived))
    values[f"{q(DATA_TAB)}!A1"] = data
    did = IDS[DATA_TAB]
    nd = max(len(data), 2)
    adds.append(_add_sheet(did, DATA_TAB, rows=max(5000, len(data) + 1000), cols=len(head_row) + 2, frozen_rows=1,
                           tab_color=TAB_GREY))
    later += [fmt(did, 0, 1, 0, len(head_row), bold=True, bg=SAGE, color=MUTED),
              fmt(did, 1, nd, 0, 1, num=(FMT_ISO, "DATE")), fmt(did, 1, nd, 4, 7, num=(FMT_INT, "NUMBER")),
              fmt(did, 1, nd, 7, 8, num=(FMT_CV, "NUMBER")), fmt(did, 1, nd, 8, 9, num=(FMT_INT, "NUMBER")),
              width(did, 3, 4, 280)]
    for i, m in enumerate(derived):
        later.append(fmt(did, 1, None, len(DATA_HEAD) + i, len(DATA_HEAD) + i + 1, num=METRIC_FMT[m], align="RIGHT"))
    later.append({"updateCells": {"rows": [{"values": [{"note": (
        f"出どころ: Google 広告 API（{bundle.get('対象アカウント', '')}、キャンペーンの日別）、{len(data) - 1} 行、"
        f"取得 {run_at}。Claude が ads-mcp で取得しました（読むだけ）。くわしくはタブ「{SOURCE_TAB}」にあります。")}]}],
        "fields": "note", "start": {"sheetId": did, "rowIndex": 0, "columnIndex": 0}}})

    # ================= 日別データ_詳細（階層ごとの元の行。キャンペーンの行も入れる） =================
    det_rows = list(details or []) + campaign_details(rows, cond)
    det = [DETAIL_HEAD + [f'={{"{m}";ARRAYFORMULA(IF(LEN($A$2:$A)=0,,IF(${DETAIL_SUM[RATIO[m][1]]}$2:${DETAIL_SUM[RATIO[m][1]]}=0,"—",'
                          f'${DETAIL_SUM[RATIO[m][0]]}$2:${DETAIL_SUM[RATIO[m][0]]}/${DETAIL_SUM[RATIO[m][1]]}$2:${DETAIL_SUM[RATIO[m][1]]})))}}'
                          for m in derived]]
    for r in sorted(det_rows, key=lambda x: (x["date"], x["level"], x["campaign"], x["ad_group"], x["name"])):
        det.append([r["date"], r.get("media") or "Google 広告", r["level"], r["campaign"], r["ad_group"], "'" + str(r["key"]),
                    r["name"], r.get("sub", ""), int(r["impressions"]), int(r["clicks"]), round(float(r["cost"]), 6),
                    round(float(r["conversions"]), 6), round(float(r.get("value") or 0), 6)] + [None] * len(derived))
    values[f"{q(DETAIL_TAB)}!A1"] = det
    dsid = IDS[DETAIL_TAB]
    ndt = max(len(det), 2)
    adds.append(_add_sheet(dsid, DETAIL_TAB, rows=max(5000, len(det) + 1000), cols=len(det[0]) + 2, frozen_rows=1,
                           tab_color=TAB_GREY))
    named = [{"addNamedRange": {"namedRange": {"name": name, "range": rng(dsid, None, None, ord(c) - 65, ord(c) - 64)}}}
             for c, name in DETAIL_NAMES.items()]
    later += [fmt(dsid, 0, 1, 0, len(det[0]), bold=True, bg=SAGE, color=MUTED),
              fmt(dsid, 1, ndt, 0, 1, num=(FMT_ISO, "DATE")), fmt(dsid, 1, ndt, 8, 11, num=(FMT_INT, "NUMBER")),
              fmt(dsid, 1, ndt, 11, 12, num=(FMT_CV, "NUMBER")), fmt(dsid, 1, ndt, 12, 13, num=(FMT_INT, "NUMBER"))]
    for i, m in enumerate(derived):
        later.append(fmt(dsid, 1, None, 13 + i, 14 + i, num=(METRIC_FMT[m][0], METRIC_FMT[m][1]), align="RIGHT"))

    # ================= 日別 =================
    dg = Grid()
    dy = IDS[DAILY_TAB]
    D_HEAD = 4
    d0, d1 = D_HEAD + 1, D_HEAD + ndays            # 日の行（1 始まり）
    d_tot = d1 + 1
    dg.row(1, 0, [f"日別（{LC}）　{acct}"])
    dg.row(2, 0, ["土日は色付き。「暫定」の日は、コンバージョンがこれから足されることがある日です（灰色の斜体）。"
                  "率は高いほど緑、単価は高いほど赤。分母が 0 の日の率と単価は空欄（グラフが読めるように「—」を入れない）。"])
    dg.row(D_HEAD, 0, ["日付", "曜日", *metrics, "成果の数字"])
    for i in range(ndays):
        n = d0 + i
        dg.row(n, 0, [f"={S}" if i == 0 else f"=A{n - 1}+1", f'=MID("日月火水木金土",WEEKDAY(A{n}),1)',
                      *row_metrics(n, 2, metrics, lambda m, n=n: _data_sumifs(m, f"$A{n}", f"$A{n}"), none=CHART_NONE),
                      f'=IF(A{n}>{CONF},"暫定","確定")'])
    tot = []
    cmap = {m: col(2 + i) for i, m in enumerate(metrics)}
    for m in metrics:
        if m in RATIO:
            nn, dd = RATIO[m]
            tot.append(f'=IF({cmap[dd]}{d_tot}=0,"—",{cmap[nn]}{d_tot}/{cmap[dd]}{d_tot})')
        else:
            tot.append(f"=SUM({cmap[m]}{d0}:{cmap[m]}{d1})")
    dg.row(d_tot, 0, [f"{LC}の合計", "", *tot])
    values[f"{q(DAILY_TAB)}!A1"] = dg.rows()
    nc_d = 3 + nm
    adds.append(_add_sheet(dy, DAILY_TAB, rows=60, cols=max(26, nc_d + 2), frozen_rows=D_HEAD, grid=False))
    later += [fmt(dy, None, None, None, None, font=FONT, valign="MIDDLE"),
              fmt(dy, 0, 1, 0, 1, bold=True, size=14), fmt(dy, 1, 2, 0, 1, color=MUTED),
              fmt(dy, D_HEAD - 1, D_HEAD, 0, nc_d, bold=True, bg=SAGE, wrap="WRAP", align="CENTER"),
              fmt(dy, d0 - 1, d1, 0, 1, num=(FMT_MD, "DATE"), align="LEFT"),
              fmt(dy, d0 - 1, d_tot, 1, 2, align="CENTER"), fmt(dy, d0 - 1, d_tot, nc_d - 1, nc_d, align="CENTER"),
              fmt(dy, d_tot - 1, d_tot, 0, nc_d, bold=True, bg=PALE),
              {"updateBorders": {"range": rng(dy, d_tot - 1, d_tot, 0, nc_d),
                                 "top": {"style": "SOLID", "colorStyle": {"rgbColor": INK}}}},
              {"updateBorders": {"range": rng(dy, D_HEAD - 1, d1, 0, nc_d),
                                 "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": RULE}}}},
              width(dy, 0, 1, 90), width(dy, 1, 2, 44), width(dy, 2, 2 + nm, 98), width(dy, nc_d - 1, nc_d, 90),
              height(dy, D_HEAD - 1, D_HEAD, 40)]
    for i, m in enumerate(metrics):
        later.append(fmt(dy, d0 - 1, d_tot, 2 + i, 3 + i, num=METRIC_FMT[m], align="RIGHT"))
    later += color_table(dy, d0 - 1, d1, 2, metrics)
    later.append(rule(dy, d0 - 1, d1, 0, nc_d, f"=WEEKDAY($A{d0},2)>5", bg=SAGE))
    later.append(rule(dy, d0 - 1, d1, 0, nc_d, f'=${col(nc_d - 1)}{d0}="暫定"', color=MUTED, italic=True))
    target = next((x for x in bundle.get("目標・計画との差", []) if x.get("項目") == "目標のコンバージョン単価"), None)
    if target:
        cc = 2 + metrics.index("コンバージョン単価")
        later.append(rule(dy, d0 - 1, d1, cc, cc + 1, f"=AND(ISNUMBER(${col(cc)}{d0}),${col(cc)}{d0}>{target['目標']})",
                          color=RED_TXT, bold=True))
    dom_daily = rng(dy, D_HEAD - 1, d1, 0, 1)

    def dcols(m):
        c = 2 + metrics.index(m)
        return c, c + 1

    ch_row = d_tot + 1
    for k, (title, m, color_) in enumerate((("日別のコンバージョン", "コンバージョン", LINE_GREEN),
                                             ("日別のコンバージョン率", "コンバージョン率", TEAL),
                                             ("日別のコンバージョン単価", "コンバージョン単価", NEAR_BLACK))):
        later.append(chart(dy, title, "COLUMN", dom_daily, [series(dy, D_HEAD - 1, d1, *dcols(m), "LEFT_AXIS", color=color_,
                                                                    label=True)],
                           ch_row, 0, 400, 260, offset_x=10 + 420 * k, legend="NO_LEGEND"))
    check_range(DAILY_TAB, f"A{D_HEAD}:{col(nc_d - 1)}{d1}")

    # ================= サマリー =================
    sm = Grid()
    ss = IDS[SUMMARY_TAB]
    CALC_C = 15                                    # P 列から: カードの元の数字
    calc_row = {m: 6 + i for i, m in enumerate(metrics)}
    sm.set(2, 1, f"{acct}　週次レポート" if weekly else f"{acct}　運用レポート")
    sm.set(3, 1, f'=TEXT({S},"yyyy/m/d")&{_dow(S)}&"〜 "&TEXT({E},"m/d")&{_dow(E)}&"　{LPW}（"&TEXT({PS},"m/d")&"〜"'
                 f'&TEXT({PE},"m/d")&"）と比べて"')
    sm.set(4, CALC_C, "カードの元の数字（式で計算）")
    sm.row(5, CALC_C, ["指標", LC, LP, "差", "増減率"])
    Q, R = col(CALC_C + 1), col(CALC_C + 2)
    for m in metrics:
        n = calc_row[m]
        if m in RATIO:
            a, b = calc_row[RATIO[m][0]], calc_row[RATIO[m][1]]
            cur, prv = f'=IF({Q}{b}=0,"—",{Q}{a}/{Q}{b})', f'=IF({R}{b}=0,"—",{R}{a}/{R}{b})'
        else:
            cur, prv = "=" + _data_sumifs(m, S, E), "=" + _data_sumifs(m, PS, PE)
        is_rate = METRIC_FMT[m][1] == "PERCENT"
        diff = (f'=IF(AND(ISNUMBER({Q}{n}),ISNUMBER({R}{n})),({Q}{n}-{R}{n})*100,"—")' if is_rate else
                f'=IF(AND(ISNUMBER({Q}{n}),ISNUMBER({R}{n})),{Q}{n}-{R}{n},"—")')
        sm.row(n, CALC_C, [m, cur, prv, diff, f'=IF(AND(ISNUMBER({Q}{n}),ISNUMBER({R}{n}),{R}{n}<>0),({Q}{n}-{R}{n})/{R}{n},"—")'])
    calc_end = 5 + nm

    def compare_text(m):
        n = calc_row[m]
        qq, rr = f"{Q}{n}", f"{R}{n}"
        if METRIC_FMT[m][1] == "PERCENT":
            pv = f'TEXT({rr},"0.00%")'
            return (f'=IF(NOT(ISNUMBER({rr})),"{LPW} —",IF(NOT(ISNUMBER({qq})),"{LPW} "&{pv},IF({qq}={rr},"± 0pt",'
                    f'IF({qq}>{rr},"▲ ","▼ ")&TEXT(ABS({qq}-{rr})*100,"0.00")&"pt")&"（{LPW} "&{pv}&"）"))')
        f_, u = CARD_UNIT[m]
        pv = f'TEXT({rr},"{f_}")&"{u}"'
        return (f'=IF(NOT(ISNUMBER({rr})),"{LPW} —",IF(OR(NOT(ISNUMBER({qq})),{rr}=0),"{LPW} "&{pv},IF({qq}={rr},"± 0%",'
                f'IF({qq}>{rr},"▲ ","▼ ")&TEXT(ABS({qq}/{rr}-1),"0%"))&"（{LPW} "&{pv}&"）"))')

    card_rows = [(5, CARDS_1, 22, 44), (9, CARDS_2, 15, 34)]
    for r0, names, _, _ in card_rows:
        for k, m in enumerate(names):
            c = 1 + 3 * k
            sm.set(r0, c, m)
            sm.set(r0 + 1, c, f"={Q}{calc_row[m]}")
            sm.set(r0 + 2, c, compare_text(m))
    r_chart_t = 13
    sm.set(r_chart_t, 1, "日別の費用とコンバージョン")
    r_ch0, r_ch1 = 14, 28
    secs = md_sections(md) if md else {}
    summary_lines = [_plain_line(x) for x in secs.get("いちばん大事なこと", []) if x.strip().startswith(("-", "*"))][:3]
    acts = md_actions(secs.get("ネクストアクション", []))
    r = r_ch1 + 2
    r_sum_t = r_act_t = r_act_h = None
    sum_lines_rows: List[int] = []
    if summary_lines:
        r_sum_t = r
        sm.set(r, 1, f"{LC}のまとめ")
        for i, t in enumerate(summary_lines):
            sm.set(r + 1 + i, 1, t)
            sum_lines_rows.append(r + 1 + i)
        r = r + 1 + len(summary_lines) + 1
    act_cols = [(1, 5, "何をする"), (5, 9, "なぜ"), (9, 11, "承認"), (11, 13, "いつ・何で見る")]
    act_rows: List[int] = []
    if acts:
        r_act_t, r_act_h = r, r + 1
        sm.set(r_act_t, 1, "次にやること")
        for c0, _, h in act_cols:
            sm.set(r_act_h, c0, h)
        for i, a in enumerate(acts):
            n = r_act_h + 1 + i
            act_rows.append(n)
            for (c0, _, _), v in zip(act_cols, (a["何をする"], a["なぜ"], a["承認"] or "—", a["いつ・何で評価"])):
                sm.set(n, c0, v)
        r = r_act_h + len(acts) + 2
    r_foot = r
    sm.set(r_foot, 1, f'=IF({CONF}>={E},"{LC}のコンバージョンは確定とみなしています。","コンバージョンは、クリックの数日あとに足される'
                      f'ことがあります。"&TEXT({CONF},"m/d")&" より後の日の数字は暫定で、これから増えることがあります。")')
    values[f"{q(SUMMARY_TAB)}!A1"] = sm.rows()
    adds.append(_add_sheet(ss, SUMMARY_TAB, rows=max(60, r_foot + 10), cols=CALC_C + 6, grid=False))
    later += [fmt(ss, None, None, None, None, font=FONT, valign="MIDDLE"),
              width(ss, 0, 1, 24), width(ss, 1, 13, 78), width(ss, 13, CALC_C, 40),
              width(ss, CALC_C, CALC_C + 1, 150), width(ss, CALC_C + 1, CALC_C + 5, 92),
              height(ss, 0, 1, 12), merge(ss, 1, 2, 1, 13), merge(ss, 2, 3, 1, 13),
              fmt(ss, 1, 2, 1, 2, bold=True, size=18), fmt(ss, 2, 3, 1, 2, color=MUTED, size=11),
              height(ss, 1, 2, 40), height(ss, 2, 3, 26),
              # カードの元の数字（小さく灰色で）
              fmt(ss, 3, 4, CALC_C, CALC_C + 1, color=MUTED, size=9),
              fmt(ss, 4, 5, CALC_C, CALC_C + 5, bold=True, bg=SAGE, size=9),
              fmt(ss, 5, calc_end, CALC_C, CALC_C + 5, size=9, color=MUTED),
              fmt(ss, 5, calc_end, CALC_C + 1, CALC_C + 5, align="RIGHT")]
    for m in metrics:
        n = calc_row[m] - 1
        later.append(fmt(ss, n, n + 1, CALC_C + 1, CALC_C + 3, num=METRIC_FMT[m]))
        is_rate = METRIC_FMT[m][1] == "PERCENT"
        later.append(fmt(ss, n, n + 1, CALC_C + 3, CALC_C + 4,
                         num=(FMT_PT, "NUMBER") if is_rate else ((FMT_DIFF_CV if m == "コンバージョン" else FMT_DIFF), "NUMBER")))
    later.append(fmt(ss, 5, calc_end, CALC_C + 4, CALC_C + 5, num=(FMT_RATE, "PERCENT")))
    for r0, names, big, hpx in card_rows:
        later += [height(ss, r0 - 1, r0, 24), height(ss, r0, r0 + 1, hpx), height(ss, r0 + 1, r0 + 2, 24),
                  fmt(ss, r0 - 1, r0, 1, 13, color=MUTED, size=10, bg=WHITE),
                  fmt(ss, r0, r0 + 1, 1, 13, bold=True, size=big, bg=WHITE, align="LEFT"),
                  fmt(ss, r0 + 1, r0 + 2, 1, 13, size=10, color=MUTED, bg=WHITE)]
        for k, m in enumerate(names):
            c = 1 + 3 * k
            later += [merge(ss, r0 - 1, r0, c, c + 3), merge(ss, r0, r0 + 1, c, c + 3), merge(ss, r0 + 1, r0 + 2, c, c + 3),
                      fmt(ss, r0, r0 + 1, c, c + 1, num=(CARD_FMT[m], "PERCENT" if CARD_FMT[m] == FMT_PCT else "NUMBER")),
                      frame(ss, r0 - 1, r0 + 2, c, c + 3, RULE, "SOLID")]
            ref = f"${Q}${calc_row[m]}-${R}${calc_row[m]}"
            # 比べる文の色: 良い向きは緑、悪い向きは赤（費用は色を付けない）
            later += direction_rules(ss, r0 + 1, r0 + 2, c, c + 3, m,
                                     f"IF(AND(ISNUMBER(${Q}${calc_row[m]}),ISNUMBER(${R}${calc_row[m]})),{ref},\"\")")
    later += [fmt(ss, r_chart_t - 1, r_chart_t, 1, 2, bold=True, size=12),
              chart(ss, "", "COMBO", dom_daily, [series(dy, D_HEAD - 1, d1, *dcols("費用"), "LEFT_AXIS", "COLUMN", COST_GREY),
                                                 series(dy, D_HEAD - 1, d1, *dcols("コンバージョン"), "RIGHT_AXIS", "LINE",
                                                        LINE_GREEN, label=True)],
                    r_ch0 - 1, 1, 936, 300)]  # 目盛りの題は付けない（凡例に 費用・コンバージョン と出る。要求を 1,000 字に収める）
    if r_sum_t:
        later += [fmt(ss, r_sum_t - 1, r_sum_t, 1, 2, bold=True, size=12)]
        for n in sum_lines_rows:
            later += [merge(ss, n - 1, n, 1, 13), fmt(ss, n - 1, n, 1, 13, wrap="WRAP", size=11, valign="TOP"),
                      height(ss, n - 1, n, 44)]
    if r_act_t:
        later += [fmt(ss, r_act_t - 1, r_act_t, 1, 2, bold=True, size=12),
                  fmt(ss, r_act_h - 1, r_act_h, 1, 13, bold=True, bg=SAGE, size=10)]
        for n in [r_act_h] + act_rows:
            later += [merge(ss, n - 1, n, c0, c1) for c0, c1, _ in act_cols if c1 - c0 > 1]
        for n in act_rows:
            later += [fmt(ss, n - 1, n, 1, 13, wrap="WRAP", size=10, valign="TOP"), height(ss, n - 1, n, 64)]
        later.append({"updateBorders": {"range": rng(ss, r_act_h - 1, act_rows[-1], 1, 13),
                                        "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": INK}},
                                        "top": {"style": "SOLID", "colorStyle": {"rgbColor": INK}},
                                        "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": RULE}}}})
    later += [merge(ss, r_foot - 1, r_foot, 1, 13), fmt(ss, r_foot - 1, r_foot, 1, 13, size=9, color=MUTED, wrap="WRAP")]
    check_range(SUMMARY_TAB, "B5:M11")
    check_range(SUMMARY_TAB, f"{col(CALC_C)}5:{col(CALC_C + 4)}{calc_end}")

    # ================= 週別の推移 =================
    if has_trend:
        tg = Grid()
        tw = IDS[TREND_TAB]
        wc = [1 + i for i in range(weeks)]
        T_HEAD = 6
        mrow = {m: T_HEAD + 1 + 2 * i for i, m in enumerate(metrics)}
        t_end = T_HEAD + 2 * nm
        tg.row(1, 0, [f"週別の推移（直近 {weeks} 週）　{acct}"])
        tg.row(2, 0, ["1 週は 7 日で、いちばん右が今週です。前週比は、回数・金額・単価は増減の割合、率は差（pt）です。"
                      "良い向きは緑、悪い向きは赤。費用には色を付けません。分母が 0 の週の率と単価は空欄です。"])
        tg.row(4, 0, ["週の初日"] + [f"={W0}" if i == 0 else f"={col(c - 1)}4+7" for i, c in enumerate(wc)])
        tg.row(5, 0, ["週の最終日"] + [f"={col(c)}4+6" for c in wc])
        tg.row(T_HEAD, 0, ["指標＼週"] + [f'=TEXT({col(c)}4,"m/d")&"〜"&TEXT({col(c)}5,"m/d")' for c in wc])
        for m in metrics:
            n = mrow[m]
            cells, comp = [m], ["　前週比"]
            for i, c in enumerate(wc):
                L = col(c)
                if m in RATIO:
                    a, b = mrow[RATIO[m][0]], mrow[RATIO[m][1]]
                    cells.append(f'=IF({L}{b}=0,{CHART_NONE},{L}{a}/{L}{b})')  # グラフが読む行なので "—" にしない
                else:
                    d_ = q(DATA_TAB)
                    cells.append(f'=SUMIFS({d_}!${SUM_COL[m]}:${SUM_COL[m]},{d_}!$A:$A,">="&{L}$4,{d_}!$A:$A,"<="&{L}$5)')
                if i == 0:
                    comp.append("—")
                elif METRIC_FMT[m][1] == "PERCENT":
                    P_ = col(c - 1)
                    comp.append(f'=IF(AND(ISNUMBER({L}{n}),ISNUMBER({P_}{n})),({L}{n}-{P_}{n})*100,"—")')
                else:
                    P_ = col(c - 1)
                    comp.append(f'=IF(AND(ISNUMBER({L}{n}),ISNUMBER({P_}{n}),{P_}{n}<>0),{L}{n}/{P_}{n}-1,"—")')
            tg.row(n, 0, cells)
            tg.row(n + 1, 0, comp)
        values[f"{q(TREND_TAB)}!A1"] = tg.rows()
        last = wc[-1]
        adds.append(_add_sheet(tw, TREND_TAB, rows=60, cols=max(26, last + 3), frozen_rows=T_HEAD, frozen_cols=1, grid=False))
        later += [fmt(tw, None, None, None, None, font=FONT, valign="MIDDLE"),
                  fmt(tw, 0, 1, 0, 1, bold=True, size=14), fmt(tw, 1, 2, 0, 1, color=MUTED),
                  fmt(tw, 3, 5, 0, last + 1, size=8, color=MUTED), fmt(tw, 3, 5, 1, last + 1, num=(FMT_ISO, "DATE"), align="RIGHT"),
                  fmt(tw, T_HEAD - 1, T_HEAD, 0, last + 1, bold=True, bg=SAGE, align="CENTER"),
                  fmt(tw, T_HEAD - 1, T_HEAD, last, last + 1, bg=DARK_GREEN, color=WHITE),
                  fmt(tw, T_HEAD, t_end, 1, last + 1, align="RIGHT"),
                  width(tw, 0, 1, 150), width(tw, 1, last + 1, 108),
                  {"updateBorders": {"range": rng(tw, T_HEAD - 1, t_end, 0, last + 1),
                                     "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": INK}},
                                     "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": RULE}}}}]
        for m in metrics:
            n = mrow[m] - 1
            later += [fmt(tw, n, n + 1, 0, 1, bold=True), fmt(tw, n, n + 1, 1, last + 1, num=METRIC_FMT[m]),
                      fmt(tw, n + 1, n + 2, 0, last + 1, size=9, bg=PALE, color=MUTED),
                      fmt(tw, n + 1, n + 2, 1, last + 1, num=(FMT_PT, "NUMBER") if METRIC_FMT[m][1] == "PERCENT"
                          else (FMT_RATE, "PERCENT"))]
            later += direction_rules(tw, n + 1, n + 2, 2, last + 1, m, "C" + str(n + 2))
        # 今週の列を赤い枠で目立たせる（見本の「比べる所を赤枠で」）
        later.append(frame(tw, T_HEAD - 1, t_end, last, last + 1, RED_TXT))
        dom = rng(tw, T_HEAD - 1, T_HEAD, 0, last + 1)
        rc = lambda m: (mrow[m] - 1, mrow[m], 0, last + 1)  # noqa: E731
        later += [chart(tw, "週別の費用とコンバージョン", "COMBO", dom,
                        [series(tw, *rc("費用"), "LEFT_AXIS", "COLUMN", COST_GREY),
                         series(tw, *rc("コンバージョン"), "RIGHT_AXIS", "LINE", LINE_GREEN, label=True)],
                        t_end + 1, 0, 620, 300, offset_x=10),
                  chart(tw, "週別のコンバージョン単価", "LINE", dom,
                        [series(tw, *rc("コンバージョン単価"), "LEFT_AXIS", None, NEAR_BLACK, label=True)],
                        t_end + 1, 0, 460, 300, offset_x=650, legend="NO_LEGEND")]
        check_range(TREND_TAB, f"A4:{col(last)}{t_end}")

    # ================= 階層ごとのタブ =================
    lo_c, hi_c = cond["今の期間"].split("〜")
    built_levels = []
    for level, title, lsid in LEVEL_TABS:
        if level != "キャンペーン" and details is None:
            continue
        ents: Dict[str, dict] = {}
        for r in det_rows:
            if r["level"] != level:
                continue
            e_ = ents.setdefault(str(r["key"]), {"r": r, "cur": 0.0, "prev": 0.0})
            in_cur = lo_c <= r["date"] <= hi_c
            e_["cur" if in_cur else "prev"] += float(r["cost"]) + float(r["impressions"]) * 1e-9
        order = sorted(ents.values(), key=lambda x: (-x["cur"], -x["prev"], str(x["r"]["name"])))
        shown, hidden = order[:MAX_ENTITIES], max(0, len(order) - MAX_ENTITIES)
        ident = IDENT[level]
        nI = len(ident)
        c_cur, c_prev, c_cmp = nI, nI + nm, nI + 2 * nm
        id_i = c_cmp + 4
        ncols = id_i + 1
        cm = {m: col(c_cur + i) for i, m in enumerate(metrics)}
        pm = {m: col(c_prev + i) for i, m in enumerate(metrics)}
        H = 6
        G = Grid()
        G.set(1, 0, f"{title}　{acct}")
        G.set(2, 0, f"{LC}の費用の大きい順です。見出しの ▼ から並べ替え・絞り込みができます。"
                    f"{LC}の欄の率は高いほど緑、単価は高いほど赤です。")
        if hidden:
            G.set(3, 0, f"ほかに {hidden} 件あります（費用の小さいもの）。元の行はタブ「{DETAIL_TAB}」にあります。")
        G.set(5, 0, "対象")
        G.set(5, c_cur, f'="{LC}（"&TEXT({S},"m/d")&"〜"&TEXT({E},"m/d")&"）"')
        G.set(5, c_prev, f'="{LP}（"&TEXT({PS},"m/d")&"〜"&TEXT({PE},"m/d")&"）"')
        G.set(5, c_cmp, f"{LP}比")
        G.row(H, 0, [h for h, _ in ident] + metrics + metrics +
              ["費用の差", "コンバージョンの差", "コンバージョン単価の増減率", "費用の構成比", "ID"])
        total = f'SUMIFS(D_COST,D_DATE,">="&{S},D_DATE,"<="&{E},D_LEVEL,"{level}")'
        for i, ent in enumerate(shown):
            n = H + 1 + i
            rr = ent["r"]
            crit = f',D_LEVEL,"{level}",D_ID,${col(id_i)}{n}'

            def sx(lo, hi):
                return lambda m: f'SUMIFS({D_NAME[m]},D_DATE,">="&{lo},D_DATE,"<="&{hi}{crit})'

            cpa_c, cpa_p = cm["コンバージョン単価"], pm["コンバージョン単価"]
            G.row(n, 0, [str(rr.get(k, "") or "") for _, k in ident]
                  + row_metrics(n, c_cur, metrics, sx(S, E)) + row_metrics(n, c_prev, metrics, sx(PS, PE))
                  + [f"={cm['費用']}{n}-{pm['費用']}{n}", f"={cm['コンバージョン']}{n}-{pm['コンバージョン']}{n}",
                     f'=IF(AND(ISNUMBER({cpa_c}{n}),ISNUMBER({cpa_p}{n}),N({cpa_p}{n})<>0),{cpa_c}{n}/{cpa_p}{n}-1,"—")',
                     f'=IF({total}=0,"—",{cm["費用"]}{n}/{total})', "'" + str(rr["key"])])
        if not shown:
            G.set(H + 1, 0, f"この期間の{level}の実績はありません。")
        values[f"{q(title)}!A1"] = G.rows()
        r0, r1 = H, H + len(shown)          # データの行（0 始まりの r0 から r1 の手前まで）
        last_row = max(r1, H + 1)
        adds.append(_add_sheet(lsid, title, rows=max(100, r1 + 20), cols=max(26, ncols + 1), frozen_rows=H, frozen_cols=nI,
                               grid=False))
        later += [fmt(lsid, None, None, None, None, font=FONT, valign="MIDDLE"),
                  fmt(lsid, 0, 1, 0, 1, bold=True, size=14), fmt(lsid, 1, 3, 0, 1, color=MUTED),
                  fmt(lsid, 4, 5, 0, c_cur, bold=True, bg=SAGE, align="CENTER"),
                  fmt(lsid, 4, 5, c_cur, c_prev, bold=True, bg=DARK_GREEN, color=WHITE, align="CENTER"),
                  fmt(lsid, 4, 5, c_prev, c_cmp, bold=True, bg=PREV_GREY, color=INK, align="CENTER"),
                  fmt(lsid, 4, 5, c_cmp, id_i, bold=True, bg=NEAR_BLACK, color=WHITE, align="CENTER"),
                  fmt(lsid, H - 1, H, 0, ncols, bold=True, bg=SAGE, wrap="WRAP", align="CENTER", size=9),
                  height(lsid, H - 1, H, 44),
                  width(lsid, 0, nI, 200), width(lsid, c_cur, id_i, 92), width(lsid, id_i, ncols, 36),
                  fmt(lsid, H - 1, last_row, id_i, ncols, size=6, color=MUTED)]
        if nI > 1:
            later.append(merge(lsid, 4, 5, 0, nI))
        later += [merge(lsid, 4, 5, c_cur, c_prev), merge(lsid, 4, 5, c_prev, c_cmp), merge(lsid, 4, 5, c_cmp, id_i)]
        if level in ("広告", "キーワード"):
            later.append(width(lsid, nI - 1, nI, 110))
        if shown:
            later += [fmt(lsid, r0, r1, c_cur, id_i, align="RIGHT"), fmt(lsid, r0, r1, c_prev, c_cmp, color=MUTED),
                      fmt(lsid, r0, r1, 0, nI, wrap="CLIP"),
                      fmt(lsid, r0, r1, c_cmp, c_cmp + 1, num=(FMT_DIFF, "NUMBER")),
                      fmt(lsid, r0, r1, c_cmp + 1, c_cmp + 2, num=(FMT_DIFF_CV, "NUMBER")),
                      fmt(lsid, r0, r1, c_cmp + 2, c_cmp + 3, num=(FMT_RATE, "PERCENT")),
                      fmt(lsid, r0, r1, c_cmp + 3, c_cmp + 4, num=("0.0%", "PERCENT")),
                      {"updateBorders": {"range": rng(lsid, H - 1, r1, 0, id_i),
                                         "innerHorizontal": {"style": "SOLID", "colorStyle": {"rgbColor": RULE}},
                                         "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": INK}}}}]
            for i, m in enumerate(metrics):
                later += [fmt(lsid, r0, r1, c_cur + i, c_cur + i + 1, num=METRIC_FMT[m]),
                          fmt(lsid, r0, r1, c_prev + i, c_prev + i + 1, num=METRIC_FMT[m])]
            later += color_table(lsid, r0, r1, c_cur, metrics)
            later += direction_rules(lsid, r0, r1, c_cmp + 1, c_cmp + 2, "コンバージョン", f"{col(c_cmp + 1)}{r0 + 1}")
            later += direction_rules(lsid, r0, r1, c_cmp + 2, c_cmp + 3, "コンバージョン単価", f"{col(c_cmp + 2)}{r0 + 1}")
            later += [rule(lsid, r0, r1, c_cmp + 2, c_cmp + 3, f"=AND(ISNUMBER({col(c_cmp + 2)}{r0 + 1}),{col(c_cmp + 2)}{r0 + 1}>0)",
                           bg=BAD)]
        # 前週比の塊を枠で囲む（見本の赤枠）
        later.append(frame(lsid, 4, last_row, c_cmp, id_i, RED_TXT))
        later.append({"setBasicFilter": {"filter": {"range": rng(lsid, H - 1, last_row, 0, ncols),
                                                    "sortSpecs": [{"dimensionIndex": c_cur + metrics.index("費用"),
                                                                   "sortOrder": "DESCENDING"}]}}})
        check_range(title, f"A{H}:{col(id_i)}{last_row}")
        built_levels.append(lsid)
        expect["階層ごとのタブ"].append({"タブ": title, "対象の数": len(order), "並べた数": len(shown), "見出しの行": H})

    # ================= 判断の記録・データの出どころ =================
    dec = [["日付", "判断", "誰の判断", "対象", "評価の時期", "記録の id"]]
    for x in decisions or []:
        dec.append([x.get("日", ""), x.get("判断", ""), x.get("誰の判断", ""), x.get("対象", ""), x.get("評価の時期", ""),
                    x.get("記録の id", "")])
    values[f"{q(DECISION_TAB)}!A1"] = dec
    adds.append(_add_sheet(IDS[DECISION_TAB], DECISION_TAB, rows=200, cols=8, frozen_rows=1))
    later += [fmt(IDS[DECISION_TAB], None, None, None, None, font=FONT),
              fmt(IDS[DECISION_TAB], 0, 1, 0, 6, bold=True, bg=SAGE), width(IDS[DECISION_TAB], 1, 2, 360)]
    if len(dec) == 1:
        values[f"{q(DECISION_TAB)}!A1"].append(["（まだ記録はありません。決めたことは log_decision で残すと、ここに並びます）"])
    src = [["書いた場所", "出どころ", "対象", "取り方", "問い合わせ", "条件", "取った日時", "頼んだ人", "行数", "取ったあとの加工", "注記"],
           [f"{DATA_TAB}!A1:I{len(data)}", "Google 広告 API（読むだけ）", f"{bundle.get('対象アカウント', '')} の campaign（segments.date）",
            "Claude が ads-mcp の report_numbers と report_sheet_plan で取得", "SELECT campaign.id, campaign.name, segments.date, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign "
            "WHERE segments.date BETWEEN …", f"{w_start.isoformat()}〜{e.isoformat()}（今週と前週は束 {bundle.get('束ID', '')} の行）",
            run_at or "", "運用者", len(data) - 1, "費用は micros を円に直した。並びは日付・キャンペーン名", ""],
           [f"{DETAIL_TAB}!A1:M{len(det)}", "Google 広告 API（読むだけ）",
            f"{bundle.get('対象アカウント', '')} の campaign・ad_group・ad_group_ad・keyword_view・search_term_view（segments.date）",
            "Claude が ads-mcp の report_sheet_plan で取得", "階層ごとに SELECT … segments.date, metrics.* … WHERE segments.date BETWEEN …",
            f"今 {cond['今の期間']}／比べる {cond['比較期間']}", run_at or "", "運用者", len(det) - 1,
            "費用は micros を円に直した。キャンペーンの行は日別データと同じ行", f"束 {bundle.get('束ID', '')}"]]
    values[f"{q(SOURCE_TAB)}!A1"] = src
    adds.append(_add_sheet(IDS[SOURCE_TAB], SOURCE_TAB, rows=50, cols=12, frozen_rows=1, tab_color=TAB_GREY))
    later += [fmt(IDS[SOURCE_TAB], 0, 1, 0, 11, bold=True, bg=SAGE),
              fmt(IDS[SOURCE_TAB], 1, len(src), 0, 11, wrap="WRAP", valign="TOP"), width(IDS[SOURCE_TAB], 0, 11, 160)]

    # ================= 並び・先に送る要求 =================
    by_title = {}
    for a in adds:
        p = a["addSheet"]["properties"]
        by_title[p["title"]] = a
    ordered = [by_title[t] for t in TAB_ORDER if t in by_title]
    later += [{"updateSheetProperties": {"properties": {"sheetId": a["addSheet"]["properties"]["sheetId"], "index": i},
                                         "fields": "index"}} for i, a in enumerate(ordered)]
    setup: List[dict] = [{"updateSpreadsheetProperties": {"properties": {"timeZone": "Asia/Tokyo"}, "fields": "timeZone"}}]
    if replace_sheets:
        setup.append(_add_sheet(TEMP_ID, "作り直し中", rows=10, cols=2))
    setup += [{"deleteNamedRange": {"namedRangeId": str(x)}} for x in replace_named]
    setup += [{"deleteSheet": {"sheetId": int(x)}} for x in replace_sheets]
    setup += ordered + named
    if replace_sheets:
        setup.append({"deleteSheet": {"sheetId": TEMP_ID}})
    expect["タブの並び"] = [a["addSheet"]["properties"]["title"] for a in ordered]
    return {"先に送る要求": setup, "書く値": values, "あとで送る要求": later, "確かめる範囲": expect,
            "書き方": ["1. update_spreadsheet で「先に送る要求」（時間帯・タブの追加・名前付き範囲）を送る。作り直すときは、"
                     "仮のタブを足して古いタブと名前付き範囲を消してから足し、最後に仮のタブを消す",
                     "2. 「書く値」を範囲ごとに update_formulas で書く（式は = で始まる。日付は文字のまま渡せば日付になる）",
                     "3. 「あとで送る要求」（書式・結合・グラフ・色・並べ替えの絞り込み・タブの並び）を update_spreadsheet で送る",
                     "4. get_values で「確かめる範囲」を読み、report_sheet_check(束ID, 読んだ値) で束と照らす"]}


# ---------------- 照らす ----------------

UNIT_RE = re.compile(r"\s*(円|回|件)$")


def parse_cell(s):
    t = UNIT_RE.sub("", str(s).strip()).replace(",", "").replace("pt", "")
    if t in ("", "—"):
        return None
    pct = t.endswith("%")
    t = t.rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return s
    return (v, "%") if pct else (v, "")


def _decimals(cell) -> int:
    m = re.search(r"\.(\d+)", str(cell))
    return len(m.group(1)) if m else 0


def _same(name: str, cell, exp) -> bool:
    got = parse_cell(cell)
    if got is None or exp is None:
        return got is None and exp is None
    if not isinstance(got, tuple):
        return False
    d = report.DIGITS[name]
    # シートの表示の桁が束と同じなら完全に一致。表示の桁が多いときだけ、二度の丸めの差（1 桁）を許す
    tol = 1e-9 if _decimals(cell) <= d else 10 ** -d + 1e-9
    return abs(report.hround(got[0], d) - float(exp)) <= tol


def _iso(s, days: Sequence[str] = ()) -> Optional[str]:
    """表示の日付（2026-11-12・2026/11/12・11/12）を YYYY-MM-DD に。年の無いものは days から探す。"""
    t = str(s).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        tail = f"-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        hit = [d for d in days if d.endswith(tail)]
        return hit[0] if len(hit) == 1 else None
    return None


def _detail_expect(bundle: dict, details: List[dict]) -> Dict[tuple, dict]:
    """階層・対象 ID・期間ごとに、束と同じ定義で指標を出す（シートの階層のタブと照らすため）。"""
    lo, hi = bundle["条件"]["今の期間"].split("〜")
    groups: Dict[tuple, List[dict]] = {}
    for r in details:
        period = "今" if lo <= r["date"] <= hi else "前"
        groups.setdefault((r["level"], str(r["key"]), period), []).append(r)
    return {k: report.derive(report.sums(v)) for k, v in groups.items()}


def check(bundle: dict, rows: List[List[str]], details: Optional[List[dict]] = None,
          all_rows: Optional[List[dict]] = None) -> dict:
    """シート（get_values の表示の文字）を、束と保存した行に照らす。表は見出しの行で見分ける。

    見るもの: サマリーのカード（指標の名前が並ぶ行と、その下の値の行）とカードの元の数字（「指標」の見出し）、日別（「日付」の見出し）、
    週別の推移（「週の初日」の行から）、階層のタブ（「キャンペーン」で始まり、指標が 2 回並んで最後が ID の見出し。前に出る方が今週）。
    """
    shown = set(report.table_metrics(not show_value(bundle)))
    want = {r["指標"]: r for r in bundle["合計"] if r["指標"] in shown}
    days = {d["日付"]: d for d in bundle.get("日別", [])}
    out, bad = [], []
    mode, head = None, []
    card: List[tuple] = []
    wk_s: Dict[int, str] = {}
    wk_e: Dict[int, str] = {}
    lv: dict = {}
    exp_d: Dict[tuple, dict] = {}
    seen_tot, seen_day = set(), set()

    def put(where, name, cell, exp):
        ok = _same(name, cell, exp)
        item = {"表": where, "指標": name, "シート": cell, "束": exp, "一致": ok}
        out.append(item)
        if not ok:
            bad.append(item)

    def cell(r, i):
        return r[i] if i < len(r) else ""

    for raw in rows:
        r = ["" if c is None else str(c) for c in (raw or [])]
        if not any(x.strip() for x in r):
            mode = None
            continue
        c0 = r[0].strip()
        if c0 == "指標":
            mode, head = "合計", r
            continue
        if c0 == "日付":
            mode, head = "日別", r
            continue
        if c0 == "週の初日":
            mode = "推移"
            wk_s = {i: _iso(x) for i, x in enumerate(r) if i and _iso(x)}
            wk_e = {}
            continue
        if mode == "推移" and c0 == "週の最終日":
            wk_e = {i: _iso(x) for i, x in enumerate(r) if i and _iso(x)}
            continue
        if c0 == "キャンペーン" and r[-1].strip() == "ID" and r.count("費用") == 2:
            idx = [i for i, h in enumerate(r) if h == "費用"]
            first: Dict[str, int] = {}
            second: Dict[str, int] = {}
            for i, h in enumerate(r):
                if h in report.DIGITS:
                    (second if h in first else first).setdefault(h, i)
            level = next((x for x in ("広告", "キーワード", "検索語句") if x in r[:idx[0]]), None)
            level = level or ("広告グループ" if "広告グループ" in r[:idx[0]] else "キャンペーン")
            name_i = 2 if level in ("広告", "キーワード", "検索語句") else (1 if level == "広告グループ" else 0)
            lv = {"level": level, "cur": first, "prev": second, "id": len(r) - 1, "name": name_i}
            mode = "横"
            continue
        labels = [(i, x.strip()) for i, x in enumerate(r) if x.strip()]
        if len(labels) >= 2 and all(x in report.DIGITS for _, x in labels):
            mode, card = "カード", labels
            continue
        if mode == "カード":
            for i, name in card:
                if name in want:
                    put("サマリーのカード", name, cell(r, i), want[name]["今"])
            mode = None
            continue
        if mode == "合計" and c0 in want:
            seen_tot.add(c0)
            for i, key in ((1, "今"), (2, "前")):
                put(f"合計（{key}）", c0, cell(r, i), want[c0][key])
        elif mode == "日別":
            d = _iso(c0, list(days))
            if d in days:
                seen_day.add(d)
                for i, h in enumerate(head[1:], 1):
                    if h in report.DIGITS and h in shown:
                        put(f"日別 {d}", h, cell(r, i), days[d].get(h))
        elif mode == "推移" and c0 in report.DIGITS and all_rows is not None:
            for i, ws in wk_s.items():
                we = wk_e.get(i) or (date.fromisoformat(ws) + timedelta(days=6)).isoformat()
                exp = report.derive(report.sums([x for x in all_rows if ws <= x["date"] <= we]))
                put(f"週別 {ws}〜{we}", c0, cell(r, i), report.rnd(c0, exp.get(c0)))
        elif mode == "横" and details is not None:
            key = cell(r, lv["id"]).strip().lstrip("'")
            if not key:
                continue
            if not exp_d:
                exp_d = _detail_expect(bundle, details)
            for per, cols in (("今", lv["cur"]), ("前", lv["prev"])):
                exp = exp_d.get((lv["level"], key, per), report.derive(report.sums([])))
                for h, i in cols.items():
                    put(f"{lv['level']} {cell(r, lv['name'])}（{'今週' if per == '今' else '前週'}）", h, cell(r, i),
                        report.rnd(h, exp.get(h)))
    missing = [f"合計の{n}" for n in want if n not in seen_tot] if seen_tot else []
    if seen_day:
        missing += [f"日別 {d}" for d in days if d not in seen_day]
    if not out:
        missing.append("照らせる表が見つからない（見出しの行から読んで渡す）")
    return {"結果": "全部一致" if not bad and not missing else f"{len(bad)} 件が合わない・{len(missing)} 件が無い",
            "合わない": bad, "シートに無いもの": missing, "照らした数": len(out)}
