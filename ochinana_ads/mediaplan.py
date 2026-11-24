"""予算の配分を試算する（メディアプラン）。手元の計算だけで、広告アカウントは読まない・変えない。

計算のしかた:
- 金額は円（入力も返り値も円）。入力は検査してから使う
- step_yen（既定 5 万円）ずつ、次の 1 件がいちばん安い所へ足していく「道」を作り、総額で切って案を出す。
  上限の手前で 1 回分より少なく足した回は、足した額のとおりに積む
- 上限（cap）の無い媒体は「鈍り始める額の 5 倍」で止め、仮置きと書く
- 返り値に前提・式・読み方・根拠の行を入れ、図は保存を頼まれたときだけ書く
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import kb
from .config import out_dir

DEFAULT_STEP = 50_000
NO_CAP_SAT_MULT = 5
MAX_STEPS = 20_000


def _num(name, v, *, low=0.0, high=None, allow_zero=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} は数字で渡す（受け取った値: {v!r}）") from None
    if math.isnan(x) or x < low or (x == low and not allow_zero) or (high is not None and x > high):
        raise ValueError(f"{name} は {'0 以上' if allow_zero else '0 より大きい'}{'、' + format(high, 'g') + ' 以下' if high is not None else ''}の数字で渡す（受け取った値: {v!r}）")
    return x


class Plan:
    def __init__(self, months: List[str], search_layers: List[dict], channels: List[dict], demand=None, month_weight=None,
                 step_yen=DEFAULT_STEP):
        if not months:
            raise ValueError("months（月の名前の並び。例 [\"1月\", \"2月\", …]）を渡す")
        self.M = [str(m) for m in months]
        n = len(self.M)
        d = demand or [1] * n
        if len(d) != n:
            raise ValueError(f"demand（季節の目安）は months と同じ数（{n}）で渡す")
        d = [_num("demand の値", x) for x in d]
        mean = sum(d) / n
        self.k = [x / mean for x in d]  # 季節の倍率（平均 1）
        w = month_weight or [1] * n
        if len(w) != n:
            raise ValueError(f"month_weight（月の重み）は months と同じ数（{n}）で渡す")
        self.w = [_num("month_weight の値", x) for x in w]
        self.step = _num("step_yen（1 回に足す額）", step_yen)
        self.layers, self.channels, self.notes = [], [], []
        for L in search_layers or []:
            lid = str(L.get("id") or L.get("name") or f"層{len(self.layers) + 1}")
            row = {"id": lid, "name": L.get("name", lid), "cpc": _num(f"{lid} の cpc", L.get("cpc")),
                   "cvr": _num(f"{lid} の cvr（0〜1）", L.get("cvr"), high=1), "brand": bool(L.get("brand")), "src": L.get("src", "仮置き")}
            if L.get("cap_yen") not in (None, ""):
                row["cap_yen"] = _num(f"{lid} の cap_yen", L["cap_yen"], allow_zero=True)
            else:
                for f in ("vol", "share", "ctr"):
                    if L.get(f) in (None, ""):
                        raise ValueError(f"検索の層 {lid}: cap_yen（月の上限額）か、vol（月の検索数）・share（表示される割合）・ctr を渡す")
                row.update(vol=_num(f"{lid} の vol", L["vol"]), share=_num(f"{lid} の share", L["share"], high=1),
                           ctr=_num(f"{lid} の ctr", L["ctr"], high=1))
            self.layers.append(row)
        for ch in channels or []:
            cid = str(ch.get("id") or ch.get("name") or f"媒体{len(self.channels) + 1}")
            if cid == "search":
                raise ValueError("媒体の id に search は使えない（検索の層のために取ってある）")
            sat = _num(f"{cid} の sat_yen（鈍り始める額）", ch.get("sat_yen", 500_000))
            row = {"id": cid, "name": ch.get("name", cid), "cpc": _num(f"{cid} の cpc", ch.get("cpc")),
                   "cvr": _num(f"{cid} の cvr（0〜1）", ch.get("cvr"), high=1), "sat": sat, "seasonal": bool(ch.get("seasonal")),
                   "src": ch.get("src", "仮置き")}
            if ch.get("cap_yen") in (None, ""):
                row["cap"] = sat * NO_CAP_SAT_MULT
                self.notes.append(f"{cid}: 月の上限額（cap_yen）が無いので、鈍り始める額の {NO_CAP_SAT_MULT} 倍（{int(row['cap']):,} 円）で止めた（仮置き）")
            else:
                row["cap"] = _num(f"{cid} の cap_yen", ch["cap_yen"], allow_zero=True)
            if "sat_yen" not in ch:
                self.notes.append(f"{cid}: 鈍り始める額（sat_yen）が無いので 500,000 円とした（仮置き）。実績の点があれば fit_response_curve で当てる")
            self.channels.append(row)
        if not self.layers and not self.channels:
            raise ValueError("search_layers（検索の層）か channels（検索以外の媒体）を 1 つ以上渡す")

    # 検索: 層ごとの上限と、渡した順（意図の高い順に並べる）に買う
    def search_rows(self, i):
        rows = []
        for L in self.layers:
            if "cap_yen" in L:
                cost = L["cap_yen"] * self.k[i]
                clicks = cost / L["cpc"]
            else:
                clicks = L["vol"] * self.k[i] * L["share"] * L["ctr"]
                cost = clicks * L["cpc"]
            rows.append({"id": L["id"], "cost": cost, "clicks": clicks, "conv": clicks * L["cvr"], "brand": L["brand"]})
        return rows

    def search(self, i, spend):
        left, conv, nb = spend, 0.0, 0.0
        for r in self.search_rows(i):
            use = min(left, r["cost"])
            f = use / r["cost"] if r["cost"] else 0
            conv += r["conv"] * f
            nb += 0 if r["brand"] else r["conv"] * f
            left -= use
        return conv, nb

    def search_cap(self, i):
        return sum(r["cost"] for r in self.search_rows(i))

    # 検索以外: 成果 ＝ r × S ×（1 − e^(−費用 ÷ S)）
    def chan(self, ch, i, x):
        r = ch["cvr"] / ch["cpc"] * (self.k[i] if ch["seasonal"] else 1)
        return r * ch["sat"] * (1 - math.exp(-x / ch["sat"]))

    def conv(self, i, alloc):
        s, nb = self.search(i, alloc.get("search", 0))
        for ch in self.channels:
            v = self.chan(ch, i, alloc.get(ch["id"], 0))
            s += v
            nb += v
        return s, nb

    def path(self):
        """1 本の道: 一定の額ずつ、月の重みで割り引いた 1 件あたりがいちばん安い所へ足していく。"""
        n = len(self.M)
        A = [dict() for _ in range(n)]
        cur = [self.conv(i, A[i])[0] for i in range(n)]
        steps, spent, total = [], 0.0, sum(cur)
        chans = ([("search", None)] if self.layers else []) + [(ch["id"], ch) for ch in self.channels]
        while len(steps) < MAX_STEPS:
            best = None
            for i in range(n):
                for cid, ch in chans:
                    have = A[i].get(cid, 0)
                    capv = self.search_cap(i) if cid == "search" else ch["cap"]
                    add = min(self.step, capv - have)
                    if add < self.step * 0.2:
                        continue
                    B = dict(A[i])
                    B[cid] = have + add
                    gain = (self.conv(i, B)[0] - cur[i]) * self.w[i]
                    if gain <= 0:
                        continue
                    cpa = add / gain
                    if best is None or cpa < best[0]:
                        best = (cpa, i, cid, add, gain / self.w[i])
            if best is None:
                break
            _, i, cid, add, g = best
            A[i][cid] = A[i].get(cid, 0) + add
            cur[i] += g
            spent += add
            total += g
            steps.append({"spent": spent, "conv": total, "month": self.M[i], "month_index": i, "channel": cid, "add": add,
                          "last_cpa": add / g if g else None})
        return steps

    def cut(self, steps, total):
        n = len(self.M)
        A = [dict() for _ in range(n)]
        spent, last = 0.0, None
        for s in steps:
            if s["spent"] > total + 1e-6:
                break
            A[s["month_index"]][s["channel"]] = A[s["month_index"]].get(s["channel"], 0) + s["add"]
            spent, last = s["spent"], s["last_cpa"]
        return A, spent, last

    def evaluate(self, A):
        rows, tot, nb = [], 0.0, 0.0
        for i, m in enumerate(self.M):
            c, b = self.conv(i, A[i])
            rows.append({"月": m, "媒体ごとの額": {k: int(round(v)) for k, v in A[i].items() if v}, "月の合計": int(round(sum(A[i].values()))),
                         "件数": round(c, 1), "指名以外の件数": round(b, 1)})
            tot += c
            nb += b
        return rows, tot, nb


def reach_table(population, channels: Dict[str, dict], budgets):
    """届く割合 ＝ 1 − e^(−その人たちへの表示回数 ÷ 人数)。予算は媒体の間で均等に割る（目安）。重なりは独立とみる。"""
    N = _num("reach の population（届けたい人の数）", population)
    if not channels:
        raise ValueError("reach の channels に {媒体: {cpm, on_target}} を渡す")
    out = []
    for bud in budgets or []:
        b = _num("reach の budgets_yen の値", bud)
        per = b / len(channels)
        miss, imps = 1.0, 0.0
        for cid, ch in channels.items():
            imp = per / _num(f"{cid} の cpm", ch.get("cpm")) * 1000 * _num(f"{cid} の on_target", ch.get("on_target", 1), high=1)
            r = 1 - math.exp(-imp / N)
            miss *= (1 - r)
            imps += imp
        reach = 1 - miss
        out.append({"月の予算": int(b), "届く割合（%）": round(reach * 100, 1), "平均の回数": round(imps / (reach * N), 1) if reach else None})
    return {"届けたい人の数": int(N), "行": out,
            "式": "届く割合 ＝ 1 − e^(−その人たちへの表示回数 ÷ 人数)。表示回数 ＝ 予算 ÷ CPM × 1,000 × 届けたい人に当たる割合。媒体の重なりは独立とみて掛け合わせる。予算は媒体の間で均等に割った目安"}


def _svg(steps, marks, unit) -> str:
    W, H, L, Bm = 720, 380, 80, 50
    xm = steps[-1]["spent"] / 10_000 * 1.05
    ym = steps[-1]["conv"] * 1.1 or 1
    X = lambda v: L + (W - L - 20) * v / xm  # noqa: E731
    Y = lambda v: H - Bm - (H - Bm - 20) * v / ym  # noqa: E731
    pts = " ".join(f"{X(s['spent'] / 10_000):.1f},{Y(s['conv']):.1f}" for s in steps)
    mk = "".join(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="6" fill="#1F6B45"/><text x="{X(x) + 8:.1f}" y="{Y(y) - 8:.1f}" font-size="13" fill="#1F6B45">{n}</text>'
                 for n, x, y in marks)
    xt = "".join(f'<text x="{X(xm * i / 5):.1f}" y="{H - Bm + 20}" font-size="12" text-anchor="middle" fill="#555">{xm * i / 5:,.0f}</text>' for i in range(6))
    yt = "".join(f'<text x="{L - 8}" y="{Y(ym * i / 4) + 4:.1f}" font-size="12" text-anchor="end" fill="#555">{ym * i / 4:,.0f}</text>' for i in range(5))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Hiragino Sans,Yu Gothic,sans-serif"><rect width="{W}" height="{H}" fill="#fff"/>'
            f'<line x1="{L}" y1="{H - Bm}" x2="{W - 20}" y2="{H - Bm}" stroke="#999"/><line x1="{L}" y1="20" x2="{L}" y2="{H - Bm}" stroke="#999"/>{xt}{yt}'
            f'<polyline points="{pts}" fill="none" stroke="#C0392B" stroke-width="2.5"/>{mk}'
            f'<text x="{(W + L) / 2}" y="{H - 12}" font-size="13" text-anchor="middle">期間の媒体費（万円）</text>'
            f'<text x="18" y="{(H - Bm) / 2}" font-size="13" transform="rotate(-90 18 {(H - Bm) / 2})" text-anchor="middle">{unit}（件）</text></svg>')


def media_plan_simulate(months, search_layers=None, channels=None, demand=None, month_weight=None, step_yen=DEFAULT_STEP,
                        current_total_yen=None, current_conversions=None, scenarios=None, targets=None, fixed_yen=None,
                        reach=None, unit="件", save_chart=False) -> dict:
    t0 = time.time()
    p = Plan(months, search_layers or [], channels or [], demand, month_weight, step_yen)
    steps = p.path()
    if not steps:
        raise ValueError("どこにも足せなかった（上限が全部 0 か、率が 0）")
    max_spent, max_conv = steps[-1]["spent"], steps[-1]["conv"]
    base_total = None if current_total_yen in (None, "") else _num("current_total_yen（いまの期間の総額）", current_total_yen)
    base_conv = None if current_conversions in (None, "") else _num("current_conversions（いまの件数）", current_conversions)
    conv_src = "渡された実績" if base_conv is not None else None
    if base_total and base_conv is None:
        A, _, _ = p.cut(steps, base_total)
        base_conv = p.evaluate(A)[1]
        conv_src = "いまの総額をこの道で配ったときの試算（実績ではない）"
    sc = scenarios or ({"A": {"total_yen": base_total, "name": "A 今の総額で配り直す"}, "B": {"ratio": 1.5, "name": "B 件数を 1.5 倍に"},
                        "C": {"ratio": 2.0, "name": "C 件数を 2 倍に"}} if base_total else {})
    out_sc, marks = {}, []
    for key, s in sc.items():
        name = s.get("name", key)
        if s.get("total_yen") not in (None, ""):
            T = _num(f"{key} の total_yen", s["total_yen"])
        else:
            if base_conv is None:
                raise ValueError(f"案 {key} の ratio を使うには current_total_yen か current_conversions が要る")
            goal = base_conv * _num(f"{key} の ratio", s.get("ratio"))
            hit = next((st for st in steps if st["conv"] >= goal), None)
            if hit is None:
                out_sc[key] = {"名前": name, "届く": False,
                               "注記": f"上限まで積んでも {max_conv:.0f} 件（{max_spent:,.0f} 円）で、{s['ratio']:g} 倍の {goal:.0f} 件に届かない。地域や媒体の上限を広げる案を別に考える"}
                continue
            T = hit["spent"]
        A, spent, last = p.cut(steps, T)
        rows, tot, nb = p.evaluate(A)
        by: Dict[str, int] = {}
        for r in rows:
            for k, v in r["媒体ごとの額"].items():
                by[k] = by.get(k, 0) + v
        out_sc[key] = {"名前": name, "届く": True, "総額": int(round(spent)), "件数": round(tot, 1), "指名以外の件数": round(nb, 1),
                       "平均の 1 件あたり": int(round(spent / tot)) if tot else None,
                       "最後の 1 回分の 1 件あたり": int(round(last)) if last else None, "媒体ごとの額": by, "月ごと": rows}
        marks.append((name[:1], spent / 10_000, tot))
    inv = []
    for t in targets or []:
        tv = _num("targets の値", t)
        hit = next((st for st in steps if st["conv"] >= tv), None)
        inv.append({"件数": tv, "要る媒体費": int(round(hit["spent"])) if hit else "届かない（上限まで積んでも足りない）",
                    "そのときの最後の 1 回分の 1 件あたり": int(round(hit["last_cpa"])) if hit else None})
    fixed = {k: [int(_num(f"fixed_yen の {k}", x, allow_zero=True)) for x in v] for k, v in (fixed_yen or {}).items()}
    stride = max(1, len(steps) // 80)
    out: Dict[str, object] = {
        "対象アカウント": "なし（計算。広告アカウントは読まない・変えない）",
        "期間": f"{p.M[0]}〜{p.M[-1]}（{len(p.M)} か月）",
        "単位": f"金額は円、件数は「{unit}」",
        "上限まで積んだとき": {"総額": int(round(max_spent)), "件数": round(max_conv, 1)},
        "いま": {"総額": int(base_total) if base_total else None, "件数": round(base_conv, 1) if base_conv is not None else None,
               "件数の出どころ": conv_src},
        "案": out_sc or "current_total_yen か scenarios を渡すと出す",
        "逆算": inv or "targets（要る件数の並び）を渡すと出す",
        "件数に数えない決まった額": {"中身": fixed, "合計": sum(sum(v) for v in fixed.values())} if fixed else None,
        "予算と件数の曲線（間引き）": [{"総額": int(round(s["spent"])), "件数": round(s["conv"], 2),
                               "最後の 1 回分の 1 件あたり": int(round(s["last_cpa"])) if s["last_cpa"] else None} for s in steps[::stride]],
        "届く割合": reach_table(reach.get("population"), reach.get("channels"), reach.get("budgets_yen")) if reach else None,
        "前提": {"検索の層": [{k: v for k, v in L.items()} for L in p.layers], "検索以外の媒体": [{k: v for k, v in c.items()} for c in p.channels],
               "季節の倍率（平均 1）": [round(x, 3) for x in p.k], "月の重み": p.w, "1 回に足す額": int(p.step), "仮置き・補ったもの": p.notes or None},
        "式": {
            "検索の層の上限": "クリック ＝ 月の検索数 × 季節の倍率 × 表示される割合 × CTR、費用 ＝ クリック × CPC、件数 ＝ クリック × CVR（cap_yen の層は 上限額 × 季節の倍率）",
            "検索の買い方": "渡した層の順（意図の高い順に並べる）に、その月の上限まで使う",
            "検索以外": "件数 ＝ r × S ×（1 − e^(−費用 ÷ S)）、r ＝ CVR ÷ CPC（季節のある媒体は × 季節の倍率）、S ＝ 鈍り始める額",
            "配り方": f"{int(p.step):,} 円ずつ、月 × 媒体のうち、足したときの 1 件あたり（月の重みで割り引く）がいちばん安い所へ足していく。"
                      "足す順は総額によらないので、1 本の道を総額で切ると、どの予算の案も同じ物差しで出る",
        },
        "読み方": [
            "1 件あたりは平均ではなく「最後の 1 回分」で見る（非公開のメモ、00 §3-4 の限界コンバージョン単価）",
            "モデルの答えをそのまま比率にしない。役割と上限を人が先に決め、その中で確かめる（非公開のメモ）",
            "率が仮置きの媒体に増額を乗せない。仮置きの値は src に出どころを書き、実績が来たら置きかえる（非公開のメモ）",
            "指名の件数は分けて数え、純増は指名以外で見る（非公開のメモ）",
        ],
        "根拠": [kb.cite_line("20", "探されている数で頭打ち"), kb.cite_line("20", "出すほど効きが鈍る形"), kb.cite_line("20", "最後の 5 万円"),
               kb.cite_line("20", "1 本の道を総額で切れば"), kb.cite_line("20", "届く割合 ＝ 1 − e"), kb.cite_line("00", "限界コンバージョン単価")],
    }
    if save_chart:
        path = out_dir() / f"media_plan_{time.strftime('%Y%m%d_%H%M%S')}.svg"
        path.write_text(_svg(steps, marks, unit), encoding="utf-8")
        out["図"] = str(path)
    out["計算の時間（秒）"] = round(time.time() - t0, 2)
    return {k: v for k, v in out.items() if v is not None}
