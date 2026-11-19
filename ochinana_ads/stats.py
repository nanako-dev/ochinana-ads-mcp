"""計算の道具。API は呼ばない。AI に暗算させないために、式と前提も一緒に返す。"""
from __future__ import annotations

import math
from statistics import NormalDist

TEST_WITHIN_DAYS = 14      # ここまでなら、そのままテストする
PROXY_WITHIN_DAYS = 60     # ここまでなら、手前の指標か途中経過で見る
SMALL_COUNT = 5            # 成果か非成果がこれ以下の案は、確率が数件で大きく動く
STOP_PROBABILITY = 0.95    # 止めてよいと見る目安
EXACT_LIMIT = 200_000      # これより成果数が多いときは正規近似で計算する


def _rate(name: str, v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}は 0 より大きく 1 より小さい数で指定する（2% なら 0.02）") from None
    if not 0 < x < 1:
        raise ValueError(f"{name}は 0 より大きく 1 より小さい数で指定する（2% なら 0.02。受け取った値: {v}）")
    return x


def sample_size_per_variant(p1: float, p2: float, power: float = 0.8, alpha: float = 0.05) -> int:
    """2 つの率の差を見つけるのに、1 案あたり必要な量（両側検定、2 案に同じ量）。"""
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    n = z * z * (p1 * (1 - p1) + p2 * (1 - p2)) / ((p2 - p1) ** 2)
    return math.ceil(n)


def data_needed(baseline_rate, target_rate, daily_volume, power=0.8, alpha=0.05) -> dict:
    p1, p2 = _rate("今の率", baseline_rate), _rate("見つけたい率", target_rate)
    power, alpha = _rate("検出力", power), _rate("有意水準", alpha)
    if p1 == p2:
        raise ValueError("今の率と見つけたい率が同じ。差が無いと必要な量は計算できない")
    try:
        daily = float(daily_volume)
    except (TypeError, ValueError):
        raise ValueError("1 日の量は 0 より大きい数で指定する") from None
    if daily <= 0:
        raise ValueError("1 日の量は 0 より大きい数で指定する（表示・クリック・訪問など、率の分母になる数）")

    n = sample_size_per_variant(p1, p2, power, alpha)
    days = math.ceil(2 * n / daily)
    if days <= TEST_WITHIN_DAYS:
        handling = "テストする"
        reason = f"必要な量が {days} 日で集まる（{TEST_WITHIN_DAYS} 日以内）。"
    elif days <= PROXY_WITHIN_DAYS:
        handling = "手前の指標で見るか、途中経過で判断する"
        reason = (f"必要な量が集まるまで {days} 日かかる（{TEST_WITHIN_DAYS} 日を超え、{PROXY_WITHIN_DAYS} 日以内）。"
                  "決着を待つと長いので、もっと手前で数が集まる指標（クリック率など）で見るか、途中の傾向で判断する。")
    else:
        handling = "テストしない"
        reason = (f"必要な量が集まるまで {days} 日かかる（{PROXY_WITHIN_DAYS} 日を超える）。"
                  "この量ではテストが決着しない。差がもっと大きい案にするか、手前の指標に切り替えるか、テストせずに決める。")
    return {
        "単位": "率は 0〜1 の割合、量は率の分母になる数（表示・クリック・訪問など）",
        "入力": {
            "今の率": p1, "見つけたい率": p2, "1 日の量（2 案の合計）": daily,
            "検出力": power, "有意水準": alpha,
        },
        "1 案あたりに必要な量": n,
        "2 案の合計で必要な量": 2 * n,
        "1 案あたりに見込む成果の数": {"今の率の案": round(n * p1, 1), "見つけたい率の案": round(n * p2, 1)},
        "かかる日数": days,
        "扱い": handling,
        "理由": reason,
        "式": "n = (z(1 − 有意水準/2) + z(検出力))² × (p1(1 − p1) + p2(1 − p2)) ÷ (p2 − p1)²。日数 = 2n ÷ 1 日の量（切り上げ）",
        "前提": [
            "2 案に同じ量を半分ずつ配る。",
            "両側検定（良くなる方向も悪くなる方向も見る）。",
            "率は期間中ほぼ一定とみなす（曜日や季節の波は入れていない）。",
            f"扱いの線引きは {TEST_WITHIN_DAYS} 日と {PROXY_WITHIN_DAYS} 日。",
        ],
    }


def _count(name: str, v) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}は 0 以上の整数で指定する") from None
    if f < 0 or f != int(f):
        raise ValueError(f"{name}は 0 以上の整数で指定する（受け取った値: {v}）")
    return int(f)


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def probability_b_beats_a(a_success: int, a_total: int, b_success: int, b_total: int) -> float:
    """B の率が A の率を上回る確率。事前の知識は置かず（0〜1 のどの率も同じ確からしさ）、厳密に計算する。

    それぞれの率を Beta(成果 + 1, 非成果 + 1) とみて、P(B > A) を閉じた形の和で求める。
    """
    alpha_a, beta_a = a_success + 1, a_total - a_success + 1
    alpha_b, beta_b = b_success + 1, b_total - b_success + 1
    base = _log_beta(alpha_a, beta_a)
    total = 0.0
    for i in range(alpha_b):
        total += math.exp(
            _log_beta(alpha_a + i, beta_a + beta_b) - math.log(beta_b + i) - _log_beta(1 + i, beta_b) - base
        )
    return min(1.0, max(0.0, total))


def _normal_probability(a_success: int, a_total: int, b_success: int, b_total: int) -> float:
    pa, pb = a_success / a_total, b_success / b_total
    var = pa * (1 - pa) / a_total + pb * (1 - pb) / b_total
    if var <= 0:
        return 0.5 if pa == pb else (1.0 if pb > pa else 0.0)
    return NormalDist().cdf((pb - pa) / math.sqrt(var))


def compare_variants(a_success, a_total, b_success, b_total) -> dict:
    sa, na = _count("A の成果数", a_success), _count("A の全体数", a_total)
    sb, nb = _count("B の成果数", b_success), _count("B の全体数", b_total)
    for label, s, n in (("A", sa, na), ("B", sb, nb)):
        if n == 0:
            raise ValueError(f"{label} の全体数が 0。まだ比べられない")
        if s > n:
            raise ValueError(f"{label} の成果数（{s}）が全体数（{n}）より多い")
    pa, pb = sa / na, sb / nb
    if max(sa, sb) > EXACT_LIMIT:
        prob, method = _normal_probability(sa, na, sb, nb), "正規近似（件数が十分多いため）"
    else:
        prob, method = probability_b_beats_a(sa, na, sb, nb), "厳密な計算（事前の知識を置かないベイズの方法）"

    small = [label for label, s, n in (("A", sa, na), ("B", sb, nb)) if min(s, n - s) <= SMALL_COUNT]
    if small:
        stop = "まだ判断しない"
        stop_reason = (f"{'・'.join(small)} の成果または非成果が {SMALL_COUNT} 件以下。"
                       "確率は数件の違いで大きく動くので、止める判断には使えない。")
    elif prob >= STOP_PROBABILITY:
        stop = "B の勝ちとして止めてよい"
        stop_reason = f"B が上回る確率が {STOP_PROBABILITY:.0%} 以上。"
    elif prob <= 1 - STOP_PROBABILITY:
        stop = "A の勝ちとして止めてよい"
        stop_reason = f"B が上回る確率が {1 - STOP_PROBABILITY:.0%} 以下。"
    else:
        stop = "続ける"
        stop_reason = f"確率が {1 - STOP_PROBABILITY:.0%}〜{STOP_PROBABILITY:.0%} の間で、まだどちらとも言えない。"
    return {
        "単位": "率は 0〜1 の割合",
        "A": {"成果": sa, "全体": na, "率": round(pa, 6)},
        "B": {"成果": sb, "全体": nb, "率": round(pb, 6)},
        "差（B − A、ポイント）": round((pb - pa) * 100, 4),
        "差（A に対する割合、%）": round((pb / pa - 1) * 100, 1) if pa > 0 else None,
        "B が A を上回る確率": round(prob, 4),
        "計算方法": method,
        "件数は足りているか": not small,
        "止めてよいかの目安": stop,
        "理由": stop_reason,
        "前提": [
            "2 案は同じ期間・同じ条件で配信されたものとする。",
            f"止めてよいと見る線は {STOP_PROBABILITY:.0%}。",
            "途中で何度も見て、線を越えた時点で止めると、偶然の差を勝ちと見誤りやすい。必要な量は data_needed で先に決める。",
        ],
    }


# ================= 有意性の道具（significance） =================
# 率の信頼区間（ウィルソン）、2 案の差の検定、必要な件数、0 件のときの上限（3 の法則）、件数の偶然の振れ幅（ポアソン）。
# どれも決まった式で、結果を平易な一文でも返す。

MODES = {
    "率の幅": ("率の幅", "rate", "wilson", "信頼区間", "率"),
    "2案の差": ("2案の差", "2 案の差", "差", "diff", "ab", "比較"),
    "必要な件数": ("必要な件数", "必要な量", "sample", "サンプル"),
    "0件の上限": ("0件の上限", "0 件の上限", "3の法則", "3 の法則", "zero", "ゼロ"),
    "件数の振れ幅": ("件数の振れ幅", "振れ幅", "poisson", "ポアソン", "件数"),
}


def _z(confidence: float) -> float:
    return NormalDist().inv_cdf(1 - (1 - confidence) / 2)


def wilson_interval(success: int, total: int, confidence: float = 0.95) -> tuple:
    """ウィルソンの信頼区間。件数が少ない・率が 0 や 1 に近いときも幅が崩れにくい。"""
    if total <= 0:
        raise ValueError("全体数が 0。率の幅は出せない")
    z = _z(confidence)
    p = success / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_p(a: int, n1: int, b: int, n2: int) -> float:
    """2×2 の表のフィッシャーの正確検定（両側）。件数が少ないときに使う。"""
    k, n = a + b, n1 + n2
    lo, hi = max(0, k - n2), min(k, n1)
    logp = {x: _log_comb(n1, x) + _log_comb(n2, k - x) - _log_comb(n, k) for x in range(lo, hi + 1)}
    obs = logp[a]
    return min(1.0, sum(math.exp(v) for v in logp.values() if v <= obs + 1e-9))


def two_proportion_test(a_s: int, a_n: int, b_s: int, b_n: int) -> dict:
    pa, pb = a_s / a_n, b_s / b_n
    pool = (a_s + b_s) / (a_n + b_n)
    expected_min = min(a_n * pool, a_n * (1 - pool), b_n * pool, b_n * (1 - pool))
    if expected_min < 5:
        return {"p": fisher_exact_p(a_s, a_n, b_s, b_n), "方法": "フィッシャーの正確検定（期待される件数が 5 未満のため）"}
    se = math.sqrt(pool * (1 - pool) * (1 / a_n + 1 / b_n))
    if se == 0:
        return {"p": 1.0, "方法": "2 つの率の z 検定（両側）"}
    z = (pb - pa) / se
    return {"p": 2 * (1 - NormalDist().cdf(abs(z))), "方法": "2 つの率の z 検定（両側）", "z": round(z, 3)}


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k)。"""
    if lam <= 0:
        return 1.0
    if k < 0:
        return 0.0
    if lam > 1e5:
        return NormalDist(lam, math.sqrt(lam)).cdf(k + 0.5)
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
        if term == 0 and i > lam:
            break
    if total == 0:  # 下にあふれたときは対数で
        logs = [i * math.log(lam) - lam - math.lgamma(i + 1) for i in range(k + 1)]
        m = max(logs)
        total = math.exp(m) * sum(math.exp(x - m) for x in logs)
    return min(1.0, total)


def _solve(f, target: float, lo: float, hi: float) -> float:
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def poisson_interval(count: int, confidence: float = 0.95) -> tuple:
    """件数 k から、本当の平均（λ）の正確な信頼区間（ガーウッド）。"""
    a = (1 - confidence) / 2
    hi_bound = count + 20 * math.sqrt(count + 1) + 20
    upper = _solve(lambda lam: _poisson_cdf(count, lam), a, 0.0, hi_bound)
    lower = 0.0 if count == 0 else _solve(lambda lam: _poisson_cdf(count - 1, lam), 1 - a, 0.0, hi_bound)
    return lower, upper


def poisson_range(expected: float, confidence: float = 0.95) -> tuple:
    """平均が expected のとき、偶然だけで出る件数の範囲（両側）。"""
    a = (1 - confidence) / 2
    # 下の端は平均 − 6√平均 より下には無いので、そこから数える（結果は 0 から数えたときと同じで、速い）
    lo = max(0, int(expected - 6 * math.sqrt(expected))) if expected < 1e4 else 0
    while _poisson_cdf(lo, expected) < a:
        lo += 1 if expected < 1e4 else max(1, int(math.sqrt(expected) / 10))
    hi = max(lo, int(expected))
    while _poisson_cdf(hi, expected) < 1 - a:
        hi += 1 if expected < 1e4 else max(1, int(math.sqrt(expected) / 10))
    return lo, hi


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _conf(v) -> float:
    c = _rate("信頼度", v)
    return c


def significance(mode: str, success=None, total=None, a_success=None, a_total=None, b_success=None, b_total=None,
                 baseline_rate=None, target_rate=None, count=None, expected=None, confidence=0.95, power=0.8) -> dict:
    m = str(mode or "").strip().casefold().replace("　", " ")
    picked = next((k for k, names in MODES.items() if m in [n.casefold() for n in names]), None)
    if not picked:
        raise ValueError(f"mode は {' / '.join(MODES)} のどれか（受け取った値: {mode!r}）")
    conf = _conf(confidence)
    head = {"対象アカウント": "なし（計算）", "期間": "指定なし", "単位": "率は 0〜1 の割合", "使った計算": picked,
            "信頼度": conf}
    if picked == "率の幅":
        s, n = _count("成果数", success), _count("全体数", total)
        if n == 0 or s > n:
            raise ValueError("全体数は 1 以上、成果数は全体数以下で渡す")
        lo, hi = wilson_interval(s, n, conf)
        return {**head, "入力": {"成果": s, "全体": n}, "率": round(s / n, 6), "幅": [round(lo, 6), round(hi, 6)],
                "一文": f"{n:,} 件中 {s:,} 件（{_pct(s / n)}）。本当の率は {conf:.0%} の確からしさで {_pct(lo)}〜{_pct(hi)} の間。",
                "式": "ウィルソンの信頼区間", "前提": ["1 件ずつが独立で、率は期間中ほぼ一定とみなす。"]}
    if picked == "2案の差":
        sa, na = _count("A の成果数", a_success), _count("A の全体数", a_total)
        sb, nb = _count("B の成果数", b_success), _count("B の全体数", b_total)
        for label, s, n in (("A", sa, na), ("B", sb, nb)):
            if n == 0 or s > n:
                raise ValueError(f"{label} の全体数は 1 以上、成果数は全体数以下で渡す")
        t = two_proportion_test(sa, na, sb, nb)
        alpha = 1 - conf
        pa, pb = sa / na, sb / nb
        la, ha = wilson_interval(sa, na, conf)
        lb, hb = wilson_interval(sb, nb, conf)
        sig = t["p"] < alpha
        line = (f"A {_pct(pa)}・B {_pct(pb)}。p 値 {t['p']:.4f} で、"
                + (f"差は偶然では説明しにくい（有意水準 {alpha:.0%}）。" if sig else
                   f"この件数では差があるとは言えない（有意水準 {alpha:.0%}）。差が無いという意味ではない。"))
        return {**head, "入力": {"A": {"成果": sa, "全体": na}, "B": {"成果": sb, "全体": nb}},
                "A": {"率": round(pa, 6), "幅": [round(la, 6), round(ha, 6)]},
                "B": {"率": round(pb, 6), "幅": [round(lb, 6), round(hb, 6)]},
                "差（B − A、ポイント）": round((pb - pa) * 100, 4), "p 値": round(t["p"], 6), "方法": t["方法"],
                "差があると言えるか": sig, "一文": line,
                "前提": ["2 案は同じ期間・同じ条件で配信されたものとする。",
                       "途中で何度も見て、線を越えた時点で止めると偶然の差を見誤りやすい。必要な件数を先に決める（mode=必要な件数）。",
                       "B が上回る確率で見たいときは compare_variants（ベイズの方法）を使う。"]}
    if picked == "必要な件数":
        p1, p2 = _rate("今の率", baseline_rate), _rate("見つけたい率", target_rate)
        pw = _rate("検出力", power)
        if p1 == p2:
            raise ValueError("今の率と見つけたい率が同じ。差が無いと必要な件数は計算できない")
        n = sample_size_per_variant(p1, p2, pw, 1 - conf)
        return {**head, "入力": {"今の率": p1, "見つけたい率": p2, "検出力": pw, "有意水準": round(1 - conf, 6)},
                "1 案あたりに必要な件数": n, "2 案の合計": 2 * n,
                "一文": f"{_pct(p1)} と {_pct(p2)} の差を見つけるには、1 案あたり {n:,} 件（2 案で {2 * n:,} 件）の分母が要る。",
                "式": "n = (z(1 − 有意水準/2) + z(検出力))² × (p1(1 − p1) + p2(1 − p2)) ÷ (p2 − p1)²",
                "前提": ["両側検定。2 案に同じ量。日数と扱いまで見るときは data_needed を使う。"]}
    if picked == "0件の上限":
        n = _count("全体数", total)
        if n == 0:
            raise ValueError("全体数は 1 以上で渡す")
        exact = 1 - (1 - conf) ** (1 / n)
        rule3 = 3 / n
        return {**head, "入力": {"成果": 0, "全体": n},
                "上限（正確）": round(exact, 6), "上限（3 の法則）": round(rule3, 6) if conf == 0.95 else None,
                "一文": f"{n:,} 件で 0 件でも、本当の率が 0 とは限らない。{conf:.0%} の確からしさで {_pct(exact)} 以下（3 の法則なら 3 ÷ {n:,} ＝ {_pct(rule3)}）。",
                "式": "上限 ＝ 1 −（1 − 信頼度）^(1 ÷ 全体数)。信頼度 95% ならおよそ 3 ÷ 全体数（3 の法則）"}
    # 件数の振れ幅
    if count not in (None, ""):
        k = _count("件数", count)
        lo, hi = poisson_interval(k, conf)
        out = {**head, "単位": "件数（期間あたり）", "入力": {"件数": k}, "本当の平均の幅": [round(lo, 3), round(hi, 3)],
               "一文": f"{k:,} 件だった。同じ条件をくり返すと、平均は {conf:.0%} の確からしさで {lo:,.1f}〜{hi:,.1f} 件の間。",
               "式": "ポアソン分布の正確な区間（ガーウッド）"}
        if expected not in (None, ""):
            e = float(expected)
            if e <= 0:
                raise ValueError("expected（いつもの平均）は 0 より大きい数で渡す")
            rlo, rhi = poisson_range(e, conf)
            inside = rlo <= k <= rhi
            out["いつもの平均との比べ"] = {"いつもの平均": e, "偶然で出る範囲": [rlo, rhi], "範囲の中か": inside}
            out["一文"] += (f" いつもの平均 {e:,.1f} 件なら、偶然でも {rlo:,}〜{rhi:,} 件は出る。"
                          + ("今回は偶然の範囲の中。" if inside else "今回は偶然の範囲の外。何かが変わった可能性がある。"))
        out["前提"] = ["件数が独立に起きる（ポアソン分布）とみなす。曜日や季節の波は入れていない。"]
        return out
    if expected in (None, ""):
        raise ValueError("件数の振れ幅は count（今回の件数）か expected（いつもの平均）を渡す")
    e = float(expected)
    if e <= 0:
        raise ValueError("expected（いつもの平均）は 0 より大きい数で渡す")
    rlo, rhi = poisson_range(e, conf)
    return {**head, "単位": "件数（期間あたり）", "入力": {"いつもの平均": e}, "偶然で出る範囲": [rlo, rhi],
            "一文": f"平均 {e:,.1f} 件なら、何も変わっていなくても {conf:.0%} の確からしさで {rlo:,}〜{rhi:,} 件の間で振れる。",
            "式": "ポアソン分布の分位点", "前提": ["件数が独立に起きる（ポアソン分布）とみなす。"]}
