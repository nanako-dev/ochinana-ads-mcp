"""実績の月ごとの点に近似曲線を当てる（手元の計算。アカウントを読むのは、customer_id を渡したときの読み取りだけ）。

計算のしかた:
- 3 つの形を当てる: 比例 y ＝ a・x、頭打ち y ＝ c・x ÷ (x ＋ b)、鈍る形 y ＝ c・(1 − e^(−x ÷ S))（＝ r・S・(1 − e^(−x ÷ S))、r ＝ c ÷ S）。
  b・S は対数の目で探し、c は最小二乗で決める。
  当てた S は、そのまま cpa_roas_simulate の saturation_spend_yen と media_plan_simulate の sat_yen に入れられる
- 選び方: 頭打ちの形の決定係数が比例より 0.02 以上高いときだけ頭打ち。頭打ちの 2 つの形は決定係数の高い方
- 点は手で渡すか、customer_id・期間を渡して Google 広告から月ごとの費用・クリック・表示・成果を読む（読むだけ）
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from . import gaql, kb
from .config import out_dir

MIN_POINTS = 4
WEAK_R2 = 0.3
PICK_MARGIN = 0.02
GRID = 400
VALUE_NAMES = {"click": "クリック数", "clicks": "クリック数", "クリック": "クリック数", "クリック数": "クリック数",
               "imp": "表示回数", "impressions": "表示回数", "表示": "表示回数", "表示回数": "表示回数",
               "conv": "成果の件数", "conversions": "成果の件数", "成果": "成果の件数", "コンバージョン": "成果の件数", "成果の件数": "成果の件数"}


def r2(y, f):
    m = sum(y) / len(y)
    ss = sum((a - m) ** 2 for a in y)
    return 1 - sum((a - b) ** 2 for a, b in zip(y, f)) / ss if ss else 0.0


def fit_linear(x, y):
    a = sum(p * q for p, q in zip(x, y)) / (sum(p * p for p in x) or 1)
    return {"形": "比例", "a": a, "決定係数": r2(y, [a * p for p in x])}


def _grid_fit(x, y, g):
    """形 y ＝ c × g(x, k) の k を、いちばん多く使った月の 1/100〜100 倍で対数の目に探し、c は最小二乗で決める。"""
    best = None
    xs_max = max(x)
    for i in range(GRID):
        k = xs_max * 10 ** (-2 + 4 * i / (GRID - 1))
        gv = [g(p, k) for p in x]
        c = sum(q * h for q, h in zip(y, gv)) / (sum(h * h for h in gv) or 1)
        sc = r2(y, [c * h for h in gv])
        if best is None or sc > best[2]:
            best = (k, c, sc)
    return best


def fit_sat(x, y):
    b, c, sc = _grid_fit(x, y, lambda p, k: p / (p + k))
    return {"形": "頭打ち（c・x ÷ (x ＋ b)）", "c": c, "b": b, "決定係数": sc}


def fit_exp(x, y):
    S, c, sc = _grid_fit(x, y, lambda p, k: 1 - math.exp(-p / k))
    return {"形": "鈍る（c・(1 − e^(−x ÷ S))）", "c": c, "S": S, "r": c / S, "決定係数": sc}


def _monthly_from_account(customer_id, start, end, run=None) -> Dict[str, list]:
    from . import tools  # 遅く読む（API の部品を使うときだけ）

    cid = gaql.normalize_customer_id(customer_id)
    gaql.validate_period(start, end)
    q = ("SELECT segments.month, metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions FROM customer "
         f"WHERE segments.date BETWEEN '{start}' AND '{end}'")
    rows = (run or tools._search)(cid, q)
    by: Dict[str, dict] = {}
    for r in rows:
        m = str(r.segments.month)
        d = by.setdefault(m, {"cost": 0.0, "click": 0.0, "imp": 0.0, "conv": 0.0})
        d["cost"] += r.metrics.cost_micros / 1e6
        d["click"] += r.metrics.clicks
        d["imp"] += r.metrics.impressions
        d["conv"] += r.metrics.conversions
    months = sorted(by)
    return {"months": months, **{k: [by[m][k] for m in months] for k in ("cost", "click", "imp", "conv")}}


def _svg(name, ylab, pts, fit) -> str:
    W, H, L, B, ux = 640, 360, 70, 50, 1e4
    xs = [p[0] / ux for p in pts]
    ys = [p[1] for p in pts]
    xm = max(xs) * 1.3 or 1
    ym = max(ys) * 1.25 or 1
    X = lambda v: L + (W - L - 20) * v / xm  # noqa: E731
    Y = lambda v: H - B - (H - B - 20) * v / ym  # noqa: E731
    f = _predict(fit)
    line = " ".join(f"{X(xm * i / 60):.1f},{Y(f(xm * i / 60 * ux)):.1f}" for i in range(61))
    dots = "".join(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="5" fill="#1F6B45"/>' for a, b in zip(xs, ys))
    ticks = "".join(f'<text x="{X(xm * i / 4):.1f}" y="{H - B + 20}" font-size="12" text-anchor="middle" fill="#555">{xm * i / 4:,.0f}</text>' for i in range(5))
    yt = "".join(f'<text x="{L - 8}" y="{Y(ym * i / 4) + 4:.1f}" font-size="12" text-anchor="end" fill="#555">{ym * i / 4:,.0f}</text>' for i in range(5))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Hiragino Sans,Yu Gothic,sans-serif">'
            f'<rect width="{W}" height="{H}" fill="#fff"/><line x1="{L}" y1="{H - B}" x2="{W - 20}" y2="{H - B}" stroke="#999"/><line x1="{L}" y1="20" x2="{L}" y2="{H - B}" stroke="#999"/>'
            f'{ticks}{yt}<polyline points="{line}" fill="none" stroke="#C0392B" stroke-width="2.5"/>{dots}'
            f'<text x="{(W + L) / 2}" y="{H - 12}" font-size="13" text-anchor="middle" fill="#333">月の広告費（万円）</text>'
            f'<text x="16" y="{(H - B) / 2}" font-size="13" fill="#333" transform="rotate(-90 16 {(H - B) / 2})" text-anchor="middle">{ylab}</text>'
            f'<text x="{W - 24}" y="36" font-size="13" text-anchor="end" fill="#333">{name}・{fit["形"]}・決定係数 {fit["決定係数"]:.2f}</text></svg>')


def _predict(fit):
    if "b" in fit:
        return lambda v: fit["c"] * v / (v + fit["b"])
    if "S" in fit:
        return lambda v: fit["c"] * (1 - math.exp(-v / fit["S"]))
    return lambda v: fit["a"] * v


def _view(fit):
    out = {"形": fit["形"], "決定係数": round(fit["決定係数"], 3)}
    for k in ("a", "c", "b", "S", "r"):
        if k in fit:
            out[k] = round(fit[k], 6) if k in ("a", "r") else round(fit[k], 2)
    return out


def fit_response_curve(costs=None, values=None, value_name="クリック数", months=None, customer_id="", start="", end="",
                       name="", save_chart=False, run=None) -> dict:
    src = "渡された点"
    if customer_id:
        d = _monthly_from_account(customer_id, start, end, run)
        key = {"クリック数": "click", "表示回数": "imp", "成果の件数": "conv"}[VALUE_NAMES.get(str(value_name), "クリック数")]
        costs, values, months = d["cost"], d[key], d["months"]
        src = f"Google 広告 {customer_id} の月ごとの実績（{start}〜{end}、アカウント全体。読むだけ）"
        name = name or f"{customer_id}"
    if costs is None or values is None:
        raise ValueError("costs（月ごとの費用・円）と values（同じ月のクリック数など）を渡すか、customer_id・start・end を渡す")
    if len(costs) != len(values):
        raise ValueError("costs と values は同じ数で渡す")
    ylab = VALUE_NAMES.get(str(value_name), str(value_name))
    pts = []
    for i, (c, v) in enumerate(zip(costs, values)):
        if c in (None, "") or v in (None, ""):
            continue
        c, v = float(c), float(v)
        if c > 0:
            pts.append((c, v, (months[i] if months and i < len(months) else None)))
    base = {"対象アカウント": customer_id or "なし（計算）", "期間": f"{start}〜{end}" if customer_id else "渡された月",
            "単位": f"横軸は月の広告費（円）、縦軸は{ylab}", "点の出どころ": src}
    if len(pts) < MIN_POINTS:
        return {**base, "結果": f"費用が 0 より大きい月の点が {len(pts)} つしかない。{MIN_POINTS} つ未満は当てない（非公開のメモ）",
                "根拠": [kb.cite_line("20", "点が 4 つ未満の媒体は当てない")]}
    x = [p[0] for p in pts]
    y = [p[1] for p in pts]
    lin, sat, ex = fit_linear(x, y), fit_sat(x, y), fit_exp(x, y)
    curved = sat if sat["決定係数"] >= ex["決定係数"] else ex
    best = curved if curved["決定係数"] > lin["決定係数"] + PICK_MARGIN else lin
    notes = []
    if best is lin:
        notes.append(f"比例: 1 円あたり {lin['a']:.4g}（1,000 あたりの費用 約 {1000 / lin['a']:.0f} 円）" if lin["a"] else "比例の傾きが 0")
        notes.append(f"頭打ちの形は比例より決定係数が {PICK_MARGIN} 以上高くないので選ばなかった（わずかな差なら単純な比例。非公開のメモ）")
    else:
        if best is sat:
            notes.append(f"頭打ち: 上限 c ＝ 約 {sat['c']:.0f}、b（上限の半分に届く月の費用）＝ 約 {sat['b']:,.0f} 円")
        else:
            notes.append(f"鈍る形: 上限 c ＝ 約 {ex['c']:.0f}、S（鈍り始める額）＝ 約 {ex['S']:,.0f} 円")
    if best["決定係数"] < WEAK_R2:
        notes.append("当てはまりが弱い（決定係数 0.3 未満）。月によって配信の中身（キャンペーンの種類・訴求・入札）が変わっていないかを見る"
                     "（期間を分ける・別の値で当てる）。この曲線を予算の根拠にしない（非公開のメモ）")
    elif best["決定係数"] < 0.6:
        notes.append("当てはまりは中くらい（0.3〜0.6）。形の目安には使えるが、頭打ちの位置は幅を持って読む（非公開のメモ）")
    notes.append(f"点の範囲（月 {min(x):,.0f}〜{max(x):,.0f} 円）の外は、伸ばした推定（非公開のメモ）")
    hand = {"鈍る形の S（鈍り始める額）": round(ex["S"]), "その決定係数": round(ex["決定係数"], 3),
            "使い方": "cpa_roas_simulate の saturation_spend_yen、media_plan_simulate の channels の sat_yen に入れられる（どちらも同じ 1 − e^(−x ÷ S) の形）"}
    if ylab == "クリック数":
        hand["注記"] = "クリック数に当てた S。成果の件数に当てた S とは違いうる（コンバージョン率が一定なら同じ）"
    out = {**base, "点": [{"月": p[2], "費用": int(round(p[0])), ylab: p[1]} for p in pts],
           "当てた形": {"比例": _view(lin), "頭打ち": _view(sat), "鈍る": _view(ex)},
           "選んだ形": best["形"], "読み方": notes, "ほかの道具へ渡すなら": hand,
           "式": {"比例": "y ＝ a × 費用", "頭打ち": "y ＝ c × 費用 ÷ (費用 ＋ b)。b は上限 c の半分に届く月の費用",
                 "鈍る": "y ＝ c × (1 − e^(−費用 ÷ S))。r ＝ c ÷ S が最初の 1 円あたり",
                 "決定係数": "1 −（残りのずれの二乗和 ÷ 平均からのずれの二乗和）",
                 "探し方": "b・S は、いちばん多く使った月の 1/100〜100 倍を対数の目で 400 通り探し、c はそれぞれの最小二乗で決める"},
           "根拠": [kb.cite_line("20", "c が上限"), kb.cite_line("20", "決定係数"), kb.cite_line("20", "0.02 以上高いときだけ"),
                  kb.cite_line("20", "出すほど効きが鈍る形")]}
    if save_chart:
        path = out_dir() / f"curve_{(name or 'points')}_{time.strftime('%Y%m%d_%H%M%S')}.svg"
        path.write_text(_svg(name or "実績", ylab, [(p[0], p[1]) for p in pts], best), encoding="utf-8")
        out["図"] = str(path)
    return out
