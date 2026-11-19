"""レポートの「数字の束」。期間と比較期間の行から、決まった定義で数字を作る。API は呼ばない。

レポートは 3 つの層に分ける（2026-11-19、運用者と合意）。
1. 数字の束（この部品）: 指標・増減・内訳・要因の分解・目標や計画との差・成果の遅れの注意。AI に計算させない
2. 数字の突き合わせ（numcheck.py）: 書き上がった文章の数字が、束の中に全部あるかを機械で確かめる
3. 型: 読み手ごとに、どの節を・どの順で・どの長さで出すかの薄い指定（後で足す）

入口が違っても（Google 広告の API・CSV・日次のシート）、行を共通の形に直してからここに渡す:
    {"date": "YYYY-MM-DD", "key": "キャンペーンの ID か名前", "name": "表示名", "media": "Google 広告",
     "impressions": 回, "clicks": 回, "cost": 円, "conversions": 件, "value": 円}
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal
import json
import math
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

RAW = ("impressions", "clicks", "cost", "conversions", "value")

# 指標の定義（どの入口でも同じ）。単位は文章の突き合わせにも使う
DEFS = [
    # (名前, 単位, 小数の桁, 定義)
    ("表示回数", "回", 0, "広告が表示された回数"),
    ("クリック数", "回", 0, "広告がクリックされた回数"),
    ("費用", "円", 0, "使った金額"),
    ("コンバージョン", "件", 1, "成果の件数（媒体の数え方のまま。小数は媒体のアトリビューションで按分されたもの）"),
    ("コンバージョン値", "円", 0, "成果に付いた値の合計"),
    ("クリック率", "%", 2, "クリック数 ÷ 表示回数"),
    ("クリック単価", "円", 0, "費用 ÷ クリック数"),
    ("コンバージョン率", "%", 2, "コンバージョン ÷ クリック数"),
    ("コンバージョン単価", "円", 0, "費用 ÷ コンバージョン（CPA）"),
    ("ROAS", "%", 1, "コンバージョン値 ÷ 費用"),
    ("1件あたりの値", "円", 0, "コンバージョン値 ÷ コンバージョン"),
]
UNIT = {n: u for n, u, _, _ in DEFS}
DIGITS = {n: d for n, _, d, _ in DEFS}
RATE_METRICS = ("クリック率", "コンバージョン率", "ROAS")
# 表（合計・日別・内訳）に必ず並べる指標の順。運用者の指定（2026-11-19）: 表示・クリック・CTR・クリック単価・費用・
# コンバージョン・コンバージョン率・コンバージョン単価は網羅する。コンバージョン値があれば値・ROAS・1件あたりの値も
CORE_METRICS = ["表示回数", "クリック数", "クリック率", "クリック単価", "費用", "コンバージョン", "コンバージョン率", "コンバージョン単価"]
VALUE_METRICS = ["コンバージョン値", "ROAS", "1件あたりの値"]


def table_metrics(ignore_value: bool) -> List[str]:
    return CORE_METRICS + ([] if ignore_value else VALUE_METRICS)
LOWER_IS_BETTER = ("費用", "クリック単価", "コンバージョン単価")

MAX_BREAKDOWN = 20
TINY_LOG = 0.005     # 変化が ±0.5% より小さいときは、寄与の割合を出さない（割る数が 0 に近く、割合が暴れる）
LAG_DAYS = 7         # monitor.LAG_DAYS と同じ（知識ベース 90 の 5-3 の仮置き）


# ---------------- 基本の計算 ----------------

def sums(rows: Iterable[dict]) -> dict:
    t = {k: 0.0 for k in RAW}
    for r in rows:
        for k in RAW:
            t[k] += float(r.get(k) or 0)
    # 円は micros（100 万分の 1）までしか意味が無い。足し算の誤差（1,234.499999…）で四捨五入がずれないよう、そこで丸める
    return {k: round(v, 6) for k, v in t.items()}


def _div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def derive(s: dict) -> Dict[str, Optional[float]]:
    """合計から指標を作る。割れないものは None（0 と書かない）。"""
    imp, clk, cost, conv, val = (s[k] for k in RAW)
    ctr, cvr, roas = _div(clk, imp), _div(conv, clk), _div(val, cost)
    return {
        "表示回数": imp, "クリック数": clk, "費用": cost, "コンバージョン": conv, "コンバージョン値": val,
        "クリック率": ctr * 100 if ctr is not None else None,
        "クリック単価": _div(cost, clk),
        "コンバージョン率": cvr * 100 if cvr is not None else None,
        "コンバージョン単価": _div(cost, conv),
        "ROAS": roas * 100 if roas is not None else None,
        "1件あたりの値": _div(val, conv),
    }


def hround(v: float, d: int = 0):
    """四捨五入（Python の round は偶数への丸めで、1,234.5 円が 1,234 になる。シートや管理画面と合わせる）。"""
    x = Decimal(str(v)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return int(x) if d == 0 else float(x)


def rnd(name: str, v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return hround(v, DIGITS.get(name, 2))


def fmt(v: Optional[float], unit: str, digits: int = 0, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}"
    return s + (unit if unit in ("%", "pt") else f" {unit}")


def change(name: str, cur: Optional[float], prev: Optional[float]) -> dict:
    """差と増減率。率の指標（%）の差は pt（ポイント）で返す。"""
    unit = UNIT[name]
    out = {"差": None, "差の単位": "pt" if unit == "%" else unit, "増減率（%）": None}
    if cur is None or prev is None:
        return out
    out["差"] = round(cur - prev, DIGITS[name] if unit != "%" else 2)
    if out["差"] == int(out["差"]) and unit != "%":
        out["差"] = int(out["差"])
    if prev:
        out["増減率（%）"] = round((cur - prev) / abs(prev) * 100, 1)
    return out


def metric_rows(cur: dict, prev: dict, ignore_value: bool) -> List[dict]:
    rows = []
    for name, unit, digits, _ in DEFS:
        if ignore_value and name in ("コンバージョン値", "ROAS", "1件あたりの値"):
            continue
        c, p = cur[name], prev[name]
        ch = change(name, c, p)
        rows.append({"指標": name, "単位": unit, "今": rnd(name, c), "前": rnd(name, p), **ch})
    return rows


# ---------------- 要因の分解 ----------------

def _ln(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return math.log(a / b)


FACTORS = {
    # 指標: [(要因, 掛けるなら +1・割るなら -1)]。掛け算の形なので、対数の差がそのまま足し算に分かれる
    "費用": [("表示回数", 1), ("クリック率", 1), ("クリック単価", 1)],
    "コンバージョン": [("表示回数", 1), ("クリック率", 1), ("コンバージョン率", 1)],
    "コンバージョン単価": [("クリック単価", 1), ("コンバージョン率", -1)],
    "ROAS": [("1件あたりの値", 1), ("コンバージョン率", 1), ("クリック単価", -1)],
}
FORMULA = {
    "費用": "費用 ＝ 表示回数 × クリック率 × クリック単価",
    "コンバージョン": "コンバージョン ＝ 表示回数 × クリック率 × コンバージョン率",
    "コンバージョン単価": "コンバージョン単価 ＝ クリック単価 ÷ コンバージョン率",
    "ROAS": "ROAS ＝ 1件あたりの値 × コンバージョン率 ÷ クリック単価",
}


def decompose(cur: dict, prev: dict, ignore_value: bool) -> List[dict]:
    """掛け算の式に分け、それぞれの要因が変化の何割を説明するかを返す（対数の差で分けるので、要因の合計が 100% になる）。"""
    out = []
    for target, factors in FACTORS.items():
        if ignore_value and target == "ROAS":
            continue
        total = _ln(cur[target], prev[target])
        row = {"指標": target, "式": FORMULA[target], "前": rnd(target, prev[target]), "今": rnd(target, cur[target])}
        if total is None:
            row["判定"] = "分けられない（前か今が 0 か、割れない）"
            out.append(row)
            continue
        row["変化率（%）"] = round((math.exp(total) - 1) * 100, 1)
        parts = []
        for f, sgn in factors:
            lf = _ln(cur[f], prev[f])
            if lf is None:
                parts = None
                break
            p = {"要因": f, "向き": "掛ける" if sgn > 0 else "割る", "要因の変化率（%）": round((math.exp(lf) - 1) * 100, 1)}
            if abs(total) >= TINY_LOG:
                p["寄与の割合（%）"] = round(sgn * lf / total * 100)
            parts.append(p)
        if parts is None:
            row["判定"] = "分けられない（要因のどれかが 0）"
            out.append(row)
            continue
        row["要因"] = parts
        if abs(total) < TINY_LOG:
            row["読み方"] = "全体の変化が ±0.5% 未満なので、寄与の割合は出さない（割る数が 0 に近い）。要因どうしの打ち消し合いは「要因の変化率」で見る。"
        else:
            main = max(parts, key=lambda p: abs(p["寄与の割合（%）"]))
            opp = [p["要因"] for p in parts if p["寄与の割合（%）"] < 0]
            s = f"{target}の変化（{row['変化率（%）']:+.1f}%）のうち、いちばん大きいのは{main['要因']}（{main['寄与の割合（%）']}%）。"
            if opp:
                s += f"{'・'.join(opp)}は逆向きに動いて、変化を打ち消している（割合がマイナス）。"
            row["読み方"] = s
        out.append(row)
    return out


# ---------------- 内訳 ----------------

def group(rows: Iterable[dict]) -> Dict[str, dict]:
    g: Dict[str, dict] = {}
    for r in rows:
        k = str(r.get("key") or r.get("name") or "（名前なし）")
        x = g.setdefault(k, {"name": r.get("name") or k, "media": r.get("media") or "", "rows": []})
        x["rows"].append(r)
    return g


def breakdown(cur_rows: List[dict], prev_rows: List[dict], max_rows: int, ignore_value: bool) -> Tuple[List[dict], dict]:
    gc, gp = group(cur_rows), group(prev_rows)
    tc, tp = sums(cur_rows), sums(prev_rows)
    d_cost, d_conv = tc["cost"] - tp["cost"], tc["conversions"] - tp["conversions"]
    items = []
    for k in set(gc) | set(gp):
        sc = sums(gc[k]["rows"]) if k in gc else sums([])
        sp = sums(gp[k]["rows"]) if k in gp else sums([])
        if not any(sc.values()) and not any(sp.values()):
            continue
        name = (gc.get(k) or gp.get(k))["name"]
        dc, dp = derive(sc), derive(sp)
        row = {"対象": name, "ID": k}
        media = (gc.get(k) or gp.get(k))["media"]
        if media:
            row["媒体"] = media
        for m in table_metrics(ignore_value):
            row[f"{m}（今）"] = rnd(m, dc[m])
            row[f"{m}（前）"] = rnd(m, dp[m])
        row["費用の構成比（%）"] = round(sc["cost"] / tc["cost"] * 100, 1) if tc["cost"] else None
        row["費用の変化への寄与（%）"] = round((sc["cost"] - sp["cost"]) / d_cost * 100) if abs(d_cost) >= 1 else None
        row["コンバージョンの変化への寄与（%）"] = (round((sc["conversions"] - sp["conversions"]) / d_conv * 100)
                                       if abs(d_conv) >= 0.5 else None)
        if not any(sp.values()):
            row["印"] = "比較期間に実績なし（新しく出た）"
        elif not any(sc.values()):
            row["印"] = "今の期間に実績なし（止まった）"
        items.append((sc["cost"], sp["cost"], row))
    items.sort(key=lambda x: (-x[0], -x[1]))
    shown = [r for _, _, r in items[:max_rows]]
    info = {"対象の数": len(items), "表示している数": len(shown)}
    if len(items) > max_rows:
        rest_c = sum(c for c, _, _ in items[max_rows:])
        rest_p = sum(p for _, p, _ in items[max_rows:])
        info["表示していない分の費用（今・前）"] = [hround(rest_c), hround(rest_p)]
    return shown, info


# ---------------- 目標・計画との差 ----------------

def _q(x: float, unit: str):
    """件は小数 1 桁、円は整数（「36,000.0 円」と書かせない）。"""
    return hround(x, 1) if unit == "件" else hround(x)


def targets_block(cur: dict, days: int, target_cpa: Optional[float], target_roas: Optional[float],
                  plan: Optional[dict]) -> List[dict]:
    out = []
    if target_cpa:
        a = cur["コンバージョン単価"]
        out.append({"項目": "目標のコンバージョン単価", "目標": round(target_cpa), "実績": rnd("コンバージョン単価", a), "単位": "円",
                    "差": round(a - target_cpa) if a is not None else None,
                    "目標に対する割合（%）": round(a / target_cpa * 100, 1) if a is not None else None,
                    "判定": ("割れない（コンバージョン 0 件）" if a is None else "目標以内" if a <= target_cpa else "目標を超えている")})
    if target_roas:
        a = cur["ROAS"]
        out.append({"項目": "目標の ROAS", "目標": round(target_roas, 1), "実績": rnd("ROAS", a), "単位": "%",
                    "差（pt）": round(a - target_roas, 1) if a is not None else None,
                    "判定": ("割れない（費用 0）" if a is None else "目標以上" if a >= target_roas else "目標に届いていない")})
    if plan:
        for key, label, unit in (("cost", "費用", "円"), ("conversions", "コンバージョン", "件")):
            goal = plan.get(key)
            if not goal:
                continue
            actual = plan["actual"][key]
            expected = goal * plan["elapsed_days"] / plan["days"]
            pace = actual / expected * 100 if expected else None
            row = {"項目": f"計画の{label}", "計画（期間の全体）": _q(goal, unit),
                   "計画の期間": f"{plan['start']}〜{plan['end']}（{plan['days']} 日のうち {plan['elapsed_days']} 日経過）",
                   "計画の日数": plan["days"], "経過日数": plan["elapsed_days"],
                   "経過分の実績": _q(actual, unit), "単位": unit,
                   "経過日数で按分した計画": _q(expected, unit),
                   "進み具合（%）": round(pace, 1) if pace is not None else None,
                   "計画に対する消化（%）": round(actual / goal * 100, 1),
                   "いまのペースでの着地": _q(actual / plan["elapsed_days"] * plan["days"], unit)}
            if pace is not None:
                # 帯は calc.budget_pace と同じ（実務家の公開ツールの標準。公式の基準ではない）
                row["帯"] = ("健全（90〜110%）" if 90 <= pace <= 110 else
                            "注意（80〜90% か 110〜120%）" if 80 <= pace <= 120 else "危険（80% 未満か 120% 超）")
            out.append(row)
    return out


# ---------------- 日別 ----------------

def daily_rows(rows: List[dict], start: date, end: date, confirmed_until: date, ignore_value: bool = True) -> List[dict]:
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(r["date"], []).append(r)
    out, window = [], []
    d = start
    while d <= end:
        s = sums(by.get(d.isoformat(), []))
        dv = derive(s)
        window.append(s)
        window = window[-7:]
        row = {"日付": d.isoformat(), "曜日": "月火水木金土日"[d.weekday()]}
        row.update({m: rnd(m, dv[m]) for m in table_metrics(ignore_value)})
        if len(window) == 7:
            w = sums_list(window)
            row["費用の7日平均"] = hround(w["cost"] / 7)
            row["コンバージョンの7日平均"] = round(w["conversions"] / 7, 2)
        if d > confirmed_until:
            row["成果"] = "未確定（遅れて足される）"
        out.append(row)
        d += timedelta(days=1)
    return out


def sums_list(items: List[dict]) -> dict:
    t = {k: 0.0 for k in RAW}
    for s in items:
        for k in RAW:
            t[k] += s[k]
    return t


# ---------------- 表（そのまま貼れる Markdown） ----------------

def _cell(v, unit="", digits=0, sign=False):
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    return fmt(v, unit, digits, sign) if unit else (f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}")


def md_totals(rows: List[dict], cur_label: str, prev_label: str) -> str:
    lines = [f"| 指標 | {cur_label} | {prev_label} | 差 | 増減率 |", "|---|--:|--:|--:|--:|"]
    for r in rows:
        u, d = r["単位"], DIGITS[r["指標"]]
        du = r["差の単位"]
        lines.append(f"| {r['指標']} | {_cell(r['今'], u, d)} | {_cell(r['前'], u, d)} | "
                     f"{_cell(r['差'], du, 2 if du == 'pt' else d, True)} | {_cell(r['増減率（%）'], '%', 1, True)} |")
    return "\n".join(lines)


def _m(name: str, v) -> str:
    return _cell(v, UNIT[name], DIGITS[name])


def md_breakdown(rows: List[dict], metrics: List[str]) -> str:
    """対象ごとに 今 と 前 の 2 行。指標は table_metrics の全部。"""
    lines = ["| 対象 | 期間 | " + " | ".join(metrics) + " | 費用の変化への寄与 |",
             "|---|---|" + "--:|" * len(metrics) + "--:|"]
    for r in rows:
        for label, key in (("今", "今"), ("前", "前")):
            cells = " | ".join(_m(m, r.get(f"{m}（{key}）")) for m in metrics)
            share = _cell(r["費用の変化への寄与（%）"], "%", 0, True) if key == "今" else ""
            lines.append(f"| {r['対象'] if key == '今' else ''} | {label} | {cells} | {share} |")
    return "\n".join(lines)


def md_daily(days: List[dict], metrics: List[str]) -> str:
    lines = ["| 日付 | 曜日 | " + " | ".join(metrics) + " | 成果 |", "|---|---|" + "--:|" * len(metrics) + "---|"]
    for d in days:
        cells = " | ".join(_m(m, d.get(m)) for m in metrics)
        lines.append(f"| {d['日付']} | {d['曜日']} | {cells} | {'未確定' if d.get('成果') else '確定'} |")
    return "\n".join(lines)


def md_decompose(rows: List[dict]) -> str:
    lines = ["| 指標 | 変化率 | 要因と寄与の割合 |", "|---|--:|---|"]
    for r in rows:
        if "要因" not in r:
            lines.append(f"| {r['指標']} | — | {r.get('判定', '')} |")
            continue
        parts = "、".join(f"{p['要因']} {p['要因の変化率（%）']:+.1f}%"
                         + (f"（寄与 {p['寄与の割合（%）']}%）" if "寄与の割合（%）" in p else "") for p in r["要因"])
        lines.append(f"| {r['指標']} | {r['変化率（%）']:+.1f}% | {parts} |")
    return "\n".join(lines)


# ---------------- 数字の一覧（突き合わせ用） ----------------

class Numbers:
    """束に入れた数字を、文章との突き合わせのために平らに並べる。"""

    def __init__(self):
        self.items: List[dict] = []

    def add(self, label: str, value, unit: str):
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        self.items.append({"項目": label, "値": value, "単位": unit})


def collect(bundle: dict) -> List[dict]:
    n = Numbers()
    for r in bundle["合計"]:
        n.add(f"合計の{r['指標']}（今）", r["今"], r["単位"])
        n.add(f"合計の{r['指標']}（前）", r["前"], r["単位"])
        n.add(f"合計の{r['指標']}の差", r["差"], r["差の単位"])
        n.add(f"合計の{r['指標']}の増減率", r["増減率（%）"], "%")
    for r in bundle.get("要因の分解", []):
        n.add(f"{r['指標']}の変化率", r.get("変化率（%）"), "%")
        for p in r.get("要因", []):
            n.add(f"{r['指標']}の分解: {p['要因']}の変化率", p["要因の変化率（%）"], "%")
            n.add(f"{r['指標']}の分解: {p['要因']}の寄与", p.get("寄与の割合（%）"), "%")
    for r in bundle.get("内訳", []):
        for k, v in r.items():
            if k in ("対象", "ID", "媒体", "印"):
                continue
            base = k.split("（")[0]
            unit = "%" if "%" in k else UNIT.get(base, "")
            n.add(f"{r['対象']}の{k}", v, unit)
    for r in bundle.get("目標・計画との差", []):
        for k, v in r.items():
            if k in ("項目", "単位", "判定", "帯", "計画の期間"):
                continue
            unit = "%" if "%" in k else ("pt" if "pt" in k else ("日" if k in ("計画の日数", "経過日数") else r.get("単位", "")))
            n.add(f"{r['項目']}の{k}", v, unit)
    for r in bundle.get("日別", []):
        for k in CORE_METRICS + VALUE_METRICS + ["費用の7日平均", "コンバージョンの7日平均"]:
            n.add(f"{r['日付']}の{k}", r.get(k), UNIT.get(k.replace("の7日平均", ""), ""))
    lag = bundle.get("成果の遅れ", {})
    n.add("未確定の日数", lag.get("未確定の日数"), "日")
    n.add("未確定の日のコンバージョン", lag.get("未確定の日のコンバージョン"), "件")
    n.add("今の期間の日数", bundle["条件"]["今の期間の日数"], "日")
    n.add("比較期間の日数", bundle["条件"]["比較期間の日数"], "日")
    return n.items


# ---------------- まとめ ----------------

def period_days(start: date, end: date) -> int:
    return (end - start).days + 1


def compare_period(start: date, end: date, mode: str) -> Tuple[date, date, str]:
    n = period_days(start, end)
    if mode in ("", "previous", "前の期間", "直前"):
        return start - timedelta(days=n), start - timedelta(days=1), f"直前の同じ日数（{n} 日）"
    if mode in ("yoy", "前年", "前年同期"):
        try:
            return start.replace(year=start.year - 1), end.replace(year=end.year - 1), "前年の同じ日付"
        except ValueError:  # 2/29
            return start - timedelta(days=365), end - timedelta(days=365), "前年の同じ日付（2/29 は 365 日前）"
    if mode in ("week", "前週", "1週前"):
        return start - timedelta(days=7), end - timedelta(days=7), "7 日前の同じ曜日"
    raise ValueError("compare は previous（直前の同じ日数）/ yoy（前年同期）/ week（7 日前）のどれか。"
                     "日付で決めるときは compare_start と compare_end を渡す")


def build(cur_rows: List[dict], prev_rows: List[dict], *, start: date, end: date, cstart: date, cend: date,
          compare_label: str, today: date, lag_days: int = LAG_DAYS, target_cpa: Optional[float] = None,
          target_roas: Optional[float] = None, plan: Optional[dict] = None, max_rows: int = MAX_BREAKDOWN,
          source: str = "", head: Optional[dict] = None, include_daily: bool = True) -> dict:
    """束を作る。head は 対象アカウント・期間・単位 などの先頭の欄（入口ごとに作って渡す）。"""
    sc, sp = sums(cur_rows), sums(prev_rows)
    cur, prev = derive(sc), derive(sp)
    ignore_value = not sc["value"] and not sp["value"] and not target_roas
    confirmed_until = today - timedelta(days=lag_days + 1) if lag_days else today - timedelta(days=1)
    bundle: Dict[str, object] = dict(head or {})
    bundle["これは何か"] = ("レポートの数字の束。数字はここで決まった定義で計算した。文章を書いたら report_number_check で、"
                       "文章の数字が全部この束にあるかを確かめる。表は「表」の欄をそのまま使い、打ち直さない。")
    bundle["条件"] = {"今の期間": f"{start}〜{end}", "今の期間の日数": period_days(start, end),
                    "比較期間": f"{cstart}〜{cend}", "比較期間の日数": period_days(cstart, cend), "比べ方": compare_label,
                    "成果の遅れとして見る日数": lag_days, "成果を確定とみなした最後の日": confirmed_until.isoformat(),
                    "入口": source}
    bundle["指標の定義"] = [{"指標": n, "単位": u, "定義": d} for n, u, _, d in DEFS
                        if not (ignore_value and n in ("コンバージョン値", "ROAS", "1件あたりの値"))]
    bundle["合計"] = metric_rows(cur, prev, ignore_value)
    bundle["要因の分解"] = decompose(cur, prev, ignore_value)
    shown, info = breakdown(cur_rows, prev_rows, max_rows, ignore_value)
    bundle["内訳の数"] = info
    bundle["内訳"] = shown
    tb = targets_block(cur, period_days(start, end), target_cpa, target_roas, plan)
    if tb:
        bundle["目標・計画との差"] = tb
    lag_dates = [d for d in date_list(start, end) if d > confirmed_until]
    lag_conv = sum(float(r.get("conversions") or 0) for r in cur_rows if r["date"] > confirmed_until.isoformat())
    lag = {"未確定の日数": len(lag_dates), "未確定の日のコンバージョン": round(lag_conv, 1)}
    if lag_dates:
        lag["注意"] = (f"{lag_dates[0]}〜{lag_dates[-1]} の {len(lag_dates)} 日は、成果が後から足される途中（lag_days={lag_days} の仮置き）。"
                     "今の期間のコンバージョン・コンバージョン率・コンバージョン単価は、確定すると良くなる向きに動く。"
                     "比較期間は確定しているので、今の期間が不利に見える。文章では「暫定」と書く。")
    else:
        lag["注意"] = "今の期間は全部、成果を確定とみなした日より前。"
    bundle["成果の遅れ"] = lag
    if include_daily:
        bundle["日別"] = daily_rows(cur_rows, start, end, confirmed_until, ignore_value)
    cur_label, prev_label = f"今（{start:%m/%d}〜{end:%m/%d}）", f"前（{cstart:%m/%d}〜{cend:%m/%d}）"
    bundle["表"] = {"合計": md_totals(bundle["合計"], cur_label, prev_label),
                   "要因の分解": md_decompose(bundle["要因の分解"])}
    metrics = table_metrics(ignore_value)
    if shown:
        bundle["表"]["内訳"] = md_breakdown(shown, metrics)
    if include_daily:
        bundle["表"]["日別"] = md_daily(bundle["日別"], metrics)
    notes = ["比べる相手は前の期間だけでなく、目標と計画も（target_cpa_yen・target_roas・plan_* を渡すと「目標・計画との差」が出る）。",
             "前後の差は原因の証明ではない。期間の中の変更は get_change_history、前後の差の確からしさは measure_effect で見る。"]
    if ignore_value:
        notes.append("コンバージョン値が今も前も 0 なので、ROAS と 1件あたりの値は出していない。")
    bundle["注意"] = notes
    nums = collect(bundle)
    key = json.dumps({"h": head, "c": bundle["条件"], "t": bundle["合計"]}, ensure_ascii=False, sort_keys=True, default=str)
    bundle["束ID"] = f"R{end:%Y%m%d}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
    bundle["数字の数"] = len(nums)
    return bundle, nums


def date_list(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range(period_days(start, end))]
