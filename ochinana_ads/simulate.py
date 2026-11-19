"""CPA と ROAS の試算（手元の計算だけ。広告アカウントは読まない・変えない）。

数字を動かすと、件数・CPA・ROAS・利益がどう変わるかを見る道具。採算の上限（損益分岐の CPA・ROAS の下限）は
calc.unit_economics を呼んで使い回す。

頭打ちの考え方の出どころ: 非公開の判断の型のメモ（外部の試算の考え方を、知識ベースが
言葉でまとめたもの）。この道具はその説明からこちらの言葉とコードで組んだもので、元のコードは写していない。
- 検索: 探されている数で頭打ち → 月のクリック数の上限（max_monthly_clicks）
- 検索以外: 出すほど効きが鈍る → 成果 ＝ r × S ×（1 − e^(−費用 ÷ S)）、r ＝ コンバージョン率 ÷ クリック単価、
  S ＝ 鈍り始める額（saturation_spend_yen）
- 1 件あたりは平均ではなく「最後に足した分」で見る（非公開のメモ、00 §3-4 の限界コンバージョン単価）
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from . import benchmarks, calc, kb

DEFAULT_MULTIPLIERS = (0.8, 0.9, 1.0, 1.1, 1.2)
DEFAULT_BUDGET_PLANS = (1.0, 1.5, 2.0)
MAX_STEPS = 9


def _num(name: str, v, *, low: float = 0.0, high: Optional[float] = None, allow_zero: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} は数字で渡す（受け取った値: {v!r}）") from None
    if math.isnan(x) or (x < low) or (x == low and not allow_zero) or (high is not None and x > high):
        rng = f"{'0 以上' if allow_zero else '0 より大きい'}" + (f"、{high:g} 以下" if high is not None else "")
        raise ValueError(f"{name} は {rng} の数字で渡す（受け取った値: {v!r}）")
    return x


def _opt(name, v, **kw):
    return None if v in (None, "") else _num(name, v, **kw)


def _yen(x: Optional[float]):
    if x is None or (isinstance(x, float) and (math.isinf(x) or math.isnan(x))):
        return None
    return int(round(x))


def _r(x: Optional[float], n: int = 2):
    if x is None or (isinstance(x, float) and (math.isinf(x) or math.isnan(x))):
        return None
    return round(x, n)


class Model:
    """予算 → クリック・成果。頭打ちの 2 つの形を足し合わせずに重ねる（検索の上限はクリックの天井、鈍りは成果の形）。"""

    def __init__(self, cpc: float, cvr: float, max_clicks: Optional[float], sat: Optional[float],
                 anchor_budget: Optional[float] = None):
        self.cpc, self.cvr, self.max_clicks, self.sat = cpc, cvr, max_clicks, sat
        self.r = cvr / cpc  # 最初の 1 円あたりの成果
        self.anchored = False
        if sat and anchor_budget:
            # いまの予算で、渡されたクリック単価・コンバージョン率（いまの平均）どおりの件数になるように r を合わせる（非公開のメモ:
            # 曲線がいまの実績から外れないように）。r は「最初の 1 円あたり」なので、いまの平均より少し良い値になる
            x = anchor_budget / sat
            self.r = (cvr / cpc) * x / (1 - math.exp(-x))
            self.anchored = True

    def conversions(self, budget: float) -> float:
        if self.sat:
            conv = self.r * self.sat * (1 - math.exp(-budget / self.sat))
        else:
            conv = self.r * budget
        if self.max_clicks is not None:
            conv = min(conv, self.max_clicks * self.cvr)
        return conv

    def clicks(self, budget: float) -> float:
        return self.conversions(budget) / self.cvr

    def capped(self, budget: float) -> bool:
        if self.max_clicks is None:
            return False
        uncapped = self.r * self.sat * (1 - math.exp(-budget / self.sat)) if self.sat else self.r * budget
        return uncapped >= self.max_clicks * self.cvr - 1e-9

    def marginal_cpa(self, budget: float) -> float:
        """最後に足した 1 円あたりの成果の逆数（限界の CPA）。頭打ちに届いていれば無限大。"""
        if self.capped(budget):
            return math.inf
        if self.sat:
            return math.exp(budget / self.sat) / self.r
        return self.cpc / self.cvr

    def cap_budget(self) -> Optional[float]:
        """検索の上限に届く予算（これより多く出しても成果は増えない）。"""
        if self.max_clicks is None:
            return None
        need = self.max_clicks * self.cvr
        if self.sat:
            top = self.r * self.sat
            if need >= top:
                return None  # 鈍りの上限の方が低い（検索の上限には届かない）
            return -self.sat * math.log(1 - need / top)
        return need / self.r

    def budget_for(self, n: float) -> Optional[float]:
        if self.max_clicks is not None and n > self.max_clicks * self.cvr + 1e-9:
            return None
        if self.sat:
            top = self.r * self.sat
            if n >= top:
                return None
            return -self.sat * math.log(1 - n / top)
        return n / self.r


def _economics(value: Optional[float], margin: Optional[float]) -> Optional[dict]:
    if value is None or margin is None:
        return None
    ue = calc.unit_economics(sale_price_yen=value, gross_margin_rate=margin)
    roas = ue["目標広告費用対効果の下限"]
    first = roas.get("初回の売上で値を付けるとき") if isinstance(roas, dict) else None
    return {"損益分岐の CPA": ue["損益分岐の獲得単価"]["成約 1 件"], "損益分岐の ROAS": first,
            "出どころ": "unit_economics（売上単価 × 粗利率。LTV・増分率・成約率は入れていない。入れるときは unit_economics を直接呼ぶ）"}


def _row(m: Model, budget: float, value: Optional[float], margin: Optional[float]) -> dict:
    conv = m.conversions(budget)
    clicks = conv / m.cvr
    row = {"広告費": _yen(budget), "クリック数": _r(clicks, 1), "成果の件数": _r(conv, 2),
           "平均の CPA": _yen(budget / conv) if conv > 0 else None,
           "平均のクリック単価": _yen(budget / clicks) if clicks > 0 else None,
           "最後に足した分の CPA": _yen(m.marginal_cpa(budget)) if not m.capped(budget) else "頭打ち（足しても増えない）",
           "検索の上限に届いた": m.capped(budget) if m.max_clicks is not None else None}
    if value is not None:
        rev = conv * value
        row.update({"売上": _yen(rev), "ROAS": _r(rev / budget, 2) if budget else None,
                    "ROAS（%）": _r(100 * rev / budget, 1) if budget else None})
        if margin is not None:
            gp = rev * margin
            row.update({"粗利": _yen(gp), "広告費を引いた利益": _yen(gp - budget)})
    return {k: v for k, v in row.items() if v is not None or k in ("平均の CPA",)}


def cpa_roas_simulate(monthly_budget_yen=None, cpc_yen=None, cvr=None, value_per_conversion_yen=None,
                      gross_margin_rate=None, target_cpa_yen=None, target_roas=None, target_conversions=None,
                      max_monthly_clicks=None, saturation_spend_yen=None, cpc_multipliers: Optional[Sequence[float]] = None,
                      cvr_multipliers: Optional[Sequence[float]] = None,
                      budget_multipliers: Optional[Sequence[float]] = None, ctr=None, industry: str = "",
                      benchmark_media: str = "google", usd_jpy=None) -> dict:
    bench = None
    filled = []
    if industry:
        bench = benchmarks.lookup(industry, benchmark_media, usd_jpy=usd_jpy)
        for name, cur, val in (("クリック単価", cpc_yen, bench["cpc_yen"]), ("コンバージョン率", cvr, bench["cvr"]), ("クリック率", ctr, bench["ctr"])):
            if cur in (None, "") and val is not None:
                filled.append(name)
        cpc_yen = cpc_yen if cpc_yen not in (None, "") else bench["cpc_yen"]
        cvr = cvr if cvr not in (None, "") else bench["cvr"]
        ctr = ctr if ctr not in (None, "") else bench["ctr"]
    cpc = _num("クリック単価（cpc_yen。無ければ industry で業種平均を入れる）", cpc_yen)
    rate = _num("コンバージョン率（cvr、0〜1。2% なら 0.02。無ければ industry で業種平均を入れる）", cvr, high=1)
    ctr_v = _opt("クリック率（ctr、0〜1）", ctr, high=1)
    budget = _opt("月の予算（monthly_budget_yen）", monthly_budget_yen)
    value = _opt("1 件あたりの売上（value_per_conversion_yen）", value_per_conversion_yen)
    margin = _opt("粗利率（gross_margin_rate、0〜1）", gross_margin_rate, high=1)
    t_cpa = _opt("目標の CPA（target_cpa_yen）", target_cpa_yen)
    t_roas = _opt("目標の ROAS（target_roas、倍。300% なら 3）", target_roas)
    t_conv = _opt("目標の件数（target_conversions）", target_conversions)
    max_clicks = _opt("月のクリック数の上限（max_monthly_clicks）", max_monthly_clicks)
    sat = _opt("鈍り始める額（saturation_spend_yen）", saturation_spend_yen)
    if budget is None and t_conv is None and t_cpa is None and t_roas is None:
        raise ValueError("monthly_budget_yen（月の予算）か、目標（target_cpa_yen・target_roas・target_conversions）のどれかを渡す")
    if t_roas is not None and value is None:
        raise ValueError("target_roas を使うときは value_per_conversion_yen（1 件あたりの売上）も渡す")
    m = Model(cpc, rate, max_clicks, sat, anchor_budget=budget)
    base_cpa = cpc / rate
    notes: List[str] = []
    out: Dict[str, object] = {
        "対象アカウント": "なし（計算。広告アカウントは読まない・変えない）",
        "期間": "月（予算と件数は 1 か月分として計算）",
        "単位": "金額は円、率は 0〜1、ROAS は倍（％も併記）",
        "入力": {"月の予算": budget, "クリック単価": cpc, "コンバージョン率": rate, "1 件あたりの売上": value, "粗利率": margin,
                "目標の CPA": t_cpa, "目標の ROAS": t_roas, "目標の件数": t_conv,
                "月のクリック数の上限": max_clicks, "鈍り始める額": sat, "クリック率": ctr_v},
    }
    if bench:
        out["業種平均"] = {**bench, "入れた値": filled or "なし（自分の数字を渡したので、業種平均は比べるためだけに付けた）",
                       "目印": {k: ("業種平均で入れた" if k in filled else "自分の数字") for k in ("クリック単価", "コンバージョン率", "クリック率")}}
    # 1. 順方向
    if budget is not None:
        fw = _row(m, budget, value, margin)
        if ctr_v and isinstance(fw, dict) and fw.get("クリック数"):
            imps = fw["クリック数"] / ctr_v
            fw["表示回数"] = _r(imps, 0)
            fw["CPM"] = _yen(budget / imps * 1000)
        out["試算（この予算のとき）"] = fw
        if max_clicks is None and sat is None:
            notes.append("頭打ちを入れていないので、件数は予算に比例し、平均の CPA と最後に足した分の CPA は同じになる。"
                         "実際には予算を増やすほど 1 件あたりは高くなる（非公開のメモ）。max_monthly_clicks か saturation_spend_yen を入れると見られる")
    else:
        out["試算（この予算のとき）"] = "monthly_budget_yen を渡していないので出していない"
    econ = _economics(value, margin)
    out["採算の線"] = econ or "value_per_conversion_yen（1 件あたりの売上）と gross_margin_rate（粗利率）を渡すと出す"
    if econ and budget is not None and isinstance(out["試算（この予算のとき）"], dict):
        cur = out["試算（この予算のとき）"]
        be = econ["損益分岐の CPA"]
        if cur.get("平均の CPA") is not None and be:
            cur["損益分岐の CPA との差"] = {"平均": _yen(be - cur["平均の CPA"]),
                                      "最後に足した分": (_yen(be - m.marginal_cpa(budget)) if not m.capped(budget) else None),
                                      "読み方": "正なら利益が出る側。平均が線の内側でも、最後に足した分が線を超えていたら増額は赤字を増やす"}
    # 2. 逆算
    rev: Dict[str, object] = {}
    goal_cpa = t_cpa
    if t_roas is not None:
        goal_cpa_from_roas = value / t_roas
        rev["目標の ROAS を CPA に直すと"] = {"CPA": _yen(goal_cpa_from_roas), "式": "CPA ＝ 1 件あたりの売上 ÷ ROAS"}
        goal_cpa = goal_cpa_from_roas if goal_cpa is None else min(goal_cpa, goal_cpa_from_roas)
        if t_cpa is not None:
            rev["注記"] = "目標の CPA と ROAS の両方を渡したので、厳しい方（安い CPA）で逆算した"
    if goal_cpa is not None:
        need_cvr = cpc / goal_cpa
        rev["目標の CPA に届くには"] = {
            "いまのクリック単価のままなら要るコンバージョン率": _r(need_cvr, 4) if need_cvr <= 1 else "1（100%）を超える＝クリック単価を下げないと届かない",
            "いまのコンバージョン率のままなら出せるクリック単価の上限": _yen(goal_cpa * rate),
            "いまの CPA との比": _r(base_cpa / goal_cpa, 3),
            "式": "要るコンバージョン率 ＝ クリック単価 ÷ 目標の CPA。クリック単価の上限 ＝ 目標の CPA × コンバージョン率",
            "注記": "ここは渡されたクリック単価（いまの平均）で計算した。頭打ちを入れているとき、予算を増やすと実際の単価は上がる",
        }
    if t_conv is not None:
        need_b = m.budget_for(t_conv)
        item = {"要る予算": _yen(need_b) if need_b is not None else "届かない（頭打ちの上限より多い）",
                "上限": (_r(min(m.max_clicks * rate if max_clicks is not None else math.inf,
                               m.r * sat if sat else math.inf), 2) if (max_clicks is not None or sat) else "頭打ちを入れていないので上限なし"),
                "式": "頭打ちなし: 予算 ＝ 件数 × CPA。鈍りあり: 予算 ＝ −S × ln(1 − 件数 ÷ (r × S))"}
        if need_b is not None:
            item["そのときの最後に足した分の CPA"] = _yen(m.marginal_cpa(need_b))
            item["そのときの平均の CPA"] = _yen(need_b / t_conv) if t_conv else None
        rev["目標の件数に要る予算"] = item
    out["逆算"] = rev or "目標（target_cpa_yen・target_roas・target_conversions）を渡すと出す"
    # 3. 感度の表（頭打ちは入れない。率と単価の効き方だけを見る）
    cm = list(cpc_multipliers or DEFAULT_MULTIPLIERS)[:MAX_STEPS]
    vm = list(cvr_multipliers or DEFAULT_MULTIPLIERS)[:MAX_STEPS]
    for name, seq in (("cpc_multipliers", cm), ("cvr_multipliers", vm)):
        if any(not isinstance(x, (int, float)) or x <= 0 for x in seq):
            raise ValueError(f"{name} は 0 より大きい倍率の並びで渡す（例 [0.8, 1.0, 1.2]）")
    table = []
    for kc in cm:
        line = {"クリック単価の倍率": kc, "クリック単価": _yen(cpc * kc)}
        for kv in vm:
            r2 = rate * kv
            cell = {"CPA": _yen(cpc * kc / r2) if r2 <= 1 else None}
            if value is not None and r2 <= 1:
                cell["ROAS"] = _r(value * r2 / (cpc * kc), 2)
            line[f"コンバージョン率 ×{kv:g}（{r2:.2%}）"] = cell
        table.append(line)
    out["感度の表"] = {
        "表": table,
        "読み方": "CPA ＝ クリック単価 ÷ コンバージョン率なので、コンバージョン率を k 倍にするのは、クリック単価を 1/k 倍にするのと同じ効き方。"
                  "どちらのてこが効くかは、どちらを動かしやすいかで決まる（入札で単価を下げると表示も減る。率は LP と成果の定義で動く）",
        "同じ効き方の例": [f"コンバージョン率 ×{k:g} ＝ クリック単価 ×{1 / k:.3f}（CPA ×{1 / k:.3f}）" for k in vm if k != 1][:4],
        "注記": "この表は頭打ちを入れていない（単価と率の効き方だけを見る表）",
    }
    # 4. 予算の案（頭打ち）
    if budget is not None:
        plans = list(budget_multipliers or DEFAULT_BUDGET_PLANS)[:MAX_STEPS]
        if any(not isinstance(x, (int, float)) or x <= 0 for x in plans):
            raise ValueError("budget_multipliers は 0 より大きい倍率の並びで渡す（例 [1, 1.5, 2]）")
        rows, prev = [], None
        for k in plans:
            b = budget * k
            row = {"案": f"予算 ×{k:g}", **_row(m, b, value, margin)}
            if prev is not None:
                d_conv = m.conversions(b) - m.conversions(prev)
                row["前の案から増やした分の CPA"] = _yen((b - prev) / d_conv) if d_conv > 1e-9 else "増えない（頭打ち）"
            rows.append(row)
            prev = b
        cap_b = m.cap_budget()
        out["予算の案"] = {
            "案": rows,
            "頭打ち": ({"検索の上限に届く予算": _yen(cap_b), "そのときの件数": _r(max_clicks * rate, 2)} if cap_b is not None else
                     ("鈍りの上限（予算をいくら積んでも超えない件数）: " + f"{m.r * sat:.1f} 件" if sat else
                      "頭打ちを入れていない（max_monthly_clicks か saturation_spend_yen を渡す）")),
            "読み方": "増額の判断は「平均の CPA」ではなく「前の案から増やした分の CPA」と「最後に足した分の CPA」で見る。"
                      "平均が目標の内側でも、増やした分が損益分岐を超えていたら、その増額は利益を減らす",
        }
    else:
        out["予算の案"] = "monthly_budget_yen を渡すと出す"
    assum = []
    if max_clicks is not None:
        assum.append("月のクリック数の上限は、渡された値（キーワードプランナーの検索数 × 表示される割合 × クリック率などから。実績が無ければ仮置き）")
    if sat is not None:
        assum.append("鈍り始める額は、渡された値。月ごとの実績の点が 4 つ以上あれば当てはめて決める（非公開のメモ）。無ければ仮置き")
    if sat is not None:
        assum.append("鈍りの形は 成果 ＝ r × S ×（1 − e^(−費用 ÷ S)）（非公開のメモ）。コンバージョン率は一定とみて、増えた費用は単価の上昇として表れる")
        assum.append("r（最初の 1 円あたりの成果）は、" + (
            "いまの予算で渡されたクリック単価・コンバージョン率どおりの件数になるように合わせた（非公開のメモ: 曲線がいまの実績から外れないように）。"
            f"r ＝ {m.r:.6f} 件／円（最初のクリック単価にすると {_yen(m.cvr / m.r)} 円）"
            if m.anchored else "予算を渡していないので、コンバージョン率 ÷ クリック単価（＝最初の 1 円の値）とみた"))
    assum.append("コンバージョン率・クリック単価は予算によらず一定とみた（頭打ちの引数を入れたときを除く）")
    out["前提"] = assum
    out["式"] = {
        "クリック数": "予算 ÷ クリック単価（上限を入れたときは上限で止める）",
        "表示回数・CPM": "表示回数 ＝ クリック数 ÷ クリック率、CPM ＝ 広告費 ÷ 表示回数 × 1,000（ctr を渡したとき）",
        "成果の件数": "クリック数 × コンバージョン率",
        "CPA": "広告費 ÷ 成果の件数 ＝ クリック単価 ÷ コンバージョン率",
        "ROAS": "売上 ÷ 広告費 ＝ 1 件あたりの売上 ÷ CPA",
        "損益分岐の CPA": "1 件あたりの売上 × 粗利率",
        "損益分岐の ROAS": "1 ÷ 粗利率",
        "利益": "売上 × 粗利率 − 広告費",
        "最後に足した分の CPA": "頭打ちなし: CPA と同じ。鈍りあり: e^(予算 ÷ S) ÷ r。検索の上限に届いたら無限大",
    }
    notes += [
        "日予算は 1 日に 2 倍まで、月に日予算の 30.4 倍まで使われうる。月の予算 ÷ 30.4 が日予算の基本（14 のルール 98・36）",
        "成果の数え方（どのコンバージョン アクションを数えるか、ビュースルーを含むか、計測の期間）で CPA と ROAS は変わる。比べるときは同じ数え方どうしで",
        "試算は予測で、実績ではない。実績が出たら、クリック単価・コンバージョン率・頭打ちの値を実績で置きかえる",
    ]
    out["読み方の注意"] = notes
    out["根拠"] = [
        kb.cite_line("20", "探されている数で頭打ち"),
        kb.cite_line("20", "出すほど効きが鈍る形"),
        kb.cite_line("20", "最後の 5 万円"),
        kb.cite_line("20", "c が上限"),
        kb.cite_line("00", "限界コンバージョン単価"),
        kb.cite("14", "98", expect=("2 倍", "30.4")),
        kb.cite_line("20", "今の結果を逸脱"),
        kb.cite_line("37", "目標 ROAS の下限"),
    ]
    out["考え方の出どころ"] = ("頭打ちと「最後に足した分」の考え方は非公開の判断の型のメモ（外部の試算の考え方を、"
                       "知識ベースがまとめたもの）。この道具はその説明からこちらで組み直したもので、元のコードは写していない")
    return out
