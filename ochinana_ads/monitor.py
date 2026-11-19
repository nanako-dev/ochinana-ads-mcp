"""変更の履歴の整形、変更の前後の比べ、いつもの水準からの外れ。API は呼ばない（行は tools.py が渡す）。

- 前後の比べは「前後の差」までを返す。変更が原因だとは言わない。
- 件数が少ないものは「判定できない」と返す。黙って「差なし」にしない（36 の 8-3: 「効かなかった」ではない）。
- 外れの所見は「AI の提案（未承認）」。止める・戻すなどの変更は、この道具ではしない。
- しきい値（1.6 倍、0.3 倍、10 件など）は知識ベース 36 の【このメモ】の目安で、公式の数字ではない。
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from . import convert, kb, stats

OPINION = "AI の提案（未承認）"
WEIGHT_ORDER = {"大": 0, "中": 1, "小": 2}
WEEKDAY_JA = "月火水木金土日"

LAG_DAYS = 7          # 90 の 5-3: 直近から除く日数 L ＝ max（5 日, 成果の 90% が出そろう日数）。不明なら 7 日と仮置き〔推論〕
MIN_EVENTS = 10       # 36 の 5-6: 「成果 10 件以上」などを前提にする（目安）。前後・比べる相手の合計がこれ未満なら判定できない
MIN_EXPECTED = 5      # いつもの水準が 1 日 5 件未満なら、0 件でも偶然の範囲に入りやすいので下振れを判定しない〔推論〕
ALPHA = 0.05          # 率の差を「偶然では説明しにくい」と見る線（両側）
CTR_ALPHA = 0.01      # 外れの通知は誤報を減らすため厳しめ（36 の 5-6 の考え方）〔推論〕
OVER_BUDGET = 1.6     # 36 の D2: 日予算の 1.6 倍超が 2 日続く【このメモ】
DAILY_CAP = 2         # 14 の 98: 1 日は日予算の 2 倍まで【公式】
UNDER_BUDGET = 0.3    # 36 の D3: 日予算の 0.3 倍未満が 2 日続く【このメモ】
CONV_SPIKE = 3        # 36 の D4: 通常の 3 倍以上（二重計上の疑い）
SAME_WEEKDAY_MIN = 3  # 同じ曜日が 3 日以上あれば、同じ曜日の平均と比べる（36 の 5-1）
MIN_BASE_DAYS = 7     # 比べる期間に表示のあった日が 7 日未満なら、いつもの水準を決めない〔推論〕

CLIENT_JA = {
    "GOOGLE_ADS_WEB_CLIENT": "管理画面（ads.google.com）",
    "GOOGLE_ADS_AUTOMATED_RULE": "自動ルール",
    "GOOGLE_ADS_SCRIPTS": "スクリプト",
    "GOOGLE_ADS_BULK_UPLOAD": "一括アップロード",
    "GOOGLE_ADS_API": "API（ほかの道具）",
    "GOOGLE_ADS_EDITOR": "Google 広告エディタ",
    "GOOGLE_ADS_MOBILE_APP": "スマホのアプリ",
    "GOOGLE_ADS_RECOMMENDATIONS": "最適化案（画面で適用）",
    "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION": "最適化案の自動適用（購読）",
    "SEARCH_ADS_360_SYNC": "検索広告 360 の同期",
    "SEARCH_ADS_360_POST": "検索広告 360 からの投稿",
    "INTERNAL_TOOL": "Google の内部の道具",
    "OTHER": "その他",
    "UNKNOWN": "不明",
    "UNSPECIFIED": "未指定",
}
# 人が画面で意図して変えたとは限らない経路（27 の 4-2 の点検の型〔推測〕）
AUTO_CLIENTS = ("GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION", "GOOGLE_ADS_AUTOMATED_RULE", "GOOGLE_ADS_SCRIPTS",
                "GOOGLE_ADS_API", "INTERNAL_TOOL", "OTHER")
RESOURCE_JA = {
    "AD": "広告", "AD_GROUP": "広告グループ", "AD_GROUP_AD": "広告（広告グループの中）",
    "AD_GROUP_CRITERION": "広告グループの条件（キーワードなど）", "AD_GROUP_BID_MODIFIER": "入札単価の調整",
    "CAMPAIGN": "キャンペーン", "CAMPAIGN_BUDGET": "予算", "CAMPAIGN_CRITERION": "キャンペーンの条件（地域・除外など）",
    "ASSET": "アセット", "CUSTOMER_ASSET": "アセット（アカウント単位）", "CAMPAIGN_ASSET": "アセット（キャンペーン単位）",
    "AD_GROUP_ASSET": "アセット（広告グループ単位）", "ASSET_SET": "アセット セット", "ASSET_SET_ASSET": "アセット セットの中身",
    "CAMPAIGN_ASSET_SET": "アセット セット（キャンペーン単位）", "FEED": "フィード", "FEED_ITEM": "フィードの項目",
    "CAMPAIGN_FEED": "フィード（キャンペーン単位）", "AD_GROUP_FEED": "フィード（広告グループ単位）",
}
OPERATION_JA = {"CREATE": "作成", "UPDATE": "更新", "REMOVE": "削除"}


NORMAL_FROM = 1000    # 平均がこれ以上なら、偶然の範囲は正規近似で出す（ポアソンとの差は 1 件ほどで、速い）


def poisson_range(expected: float) -> Tuple[int, int]:
    """平均が expected のとき、偶然だけで出る件数の範囲（95%）。小さい数は stats の正確な分位点。"""
    if expected >= NORMAL_FROM:
        half = 1.959964 * math.sqrt(expected)
        return max(0, math.floor(expected - half)), math.ceil(expected + half)
    return stats.poisson_range(expected)


# ================= 変更の履歴 =================

def _label(path: str) -> str:
    return path[: -len("_micros")] + "（円）" if path.endswith("_micros") else path


def _value_view(path: str, v):
    """金額（micros）は円に直す。数でない値（空など）はそのまま。"""
    if path.endswith("_micros") and isinstance(v, (int, float)) and not isinstance(v, bool):
        return convert.micros_to_yen(v)
    return v


def change_rows(events: List[dict], names: Dict[int, str]) -> List[dict]:
    rows = []
    for e in events:
        fields = []
        for f in e["fields"]:
            old, new = _value_view(f["path"], f["old"]), _value_view(f["path"], f["new"])
            item = {"項目": _label(f["path"])}
            if e["operation"] != "CREATE":
                item["前"] = old
            if e["operation"] != "REMOVE":
                item["後"] = new
            fields.append(item)
        rows.append({
            "日時": e["time"],
            "種類": convert.ja(RESOURCE_JA, e["resource_type"]),
            "操作": convert.ja(OPERATION_JA, e["operation"]),
            "経路": convert.ja(CLIENT_JA, e["client_type"]),
            "キャンペーン": names.get(e["campaign_id"]) if e["campaign_id"] else None,
            "変わった項目": fields,
            "対象": e["resource_name"],
            "client_type": e["client_type"],
        })
    return rows


# ================= 変更の前後 =================

def date_range(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _sum(days: Dict[str, dict], dates: Iterable[date]) -> dict:
    t = {k: 0 for k in convert.RAW_METRICS}
    for d in dates:
        m = days.get(d.isoformat())
        if m:
            for k in convert.RAW_METRICS:
                t[k] += m.get(k, 0)
    return t


def _pct_change(before: Optional[float], after: Optional[float]) -> Optional[float]:
    if before in (None, 0) or after is None:
        return None
    return round((after / before - 1) * 100, 1)


def _undecidable(reason: str) -> dict:
    return {"判定": "判定できない", "理由": reason}


def count_test(before: int, before_days: int, after: int, after_days: int) -> dict:
    """件数の前後。前の 1 日あたりの水準が続いたとき、後の期間に偶然で出る件数の範囲（ポアソン）に入るか。"""
    if before + after < MIN_EVENTS:
        return _undecidable(f"前後合わせて {before + after} 件で、{MIN_EVENTS} 件に届かない。数件の違いで大きく動く")
    if before == 0:
        return _undecidable("前の期間が 0 件で、比べる水準が無い")
    expected = before * after_days / before_days
    lo, hi = poisson_range(expected)
    if lo <= after <= hi:
        return {"判定": "偶然の範囲の中（差があるとは言えない）", "偶然で出る範囲": [lo, hi], "前の水準なら": round(expected, 1)}
    side = "上" if after > hi else "下"
    return {"判定": f"偶然の範囲を超える差（{side}）", "偶然で出る範囲": [lo, hi], "前の水準なら": round(expected, 1)}


def rate_test(b_s: int, b_n: int, a_s: int, a_n: int) -> dict:
    """率の前後。2 つの率の検定（期待される件数が少なければフィッシャーの正確検定）。"""
    if b_n == 0 or a_n == 0:
        return _undecidable("前か後の分母が 0")
    if b_s > b_n or a_s > a_n:
        return _undecidable("成果が分母より多い（1 クリックで複数の成果など）ので、率の検定にのらない")
    if b_s + a_s < MIN_EVENTS:
        return _undecidable(f"前後合わせて {b_s + a_s} 件で、{MIN_EVENTS} 件に届かない")
    t = stats.two_proportion_test(b_s, b_n, a_s, a_n)
    if t["p"] < ALPHA:
        side = "上" if a_s / a_n > b_s / b_n else "下"
        verdict = f"偶然では説明しにくい差（{side}）"
    else:
        verdict = "差があるとは言えない"
    return {"判定": verdict, "p 値": round(t["p"], 4), "方法": t["方法"]}


def unit_cost_test(b_cost: float, b_n: int, a_cost: float, a_n: int) -> dict:
    """単価の前後。分母の件数（クリック・成果）のポアソンの幅から単価の幅を出し、2 つの幅が重なるかを見る。"""
    if b_n == 0 or a_n == 0:
        return _undecidable("前か後の分母が 0 で、単価が出ない")
    if b_n + a_n < MIN_EVENTS:
        return _undecidable(f"分母が前後合わせて {b_n + a_n} 件で、{MIN_EVENTS} 件に届かない")
    def band(cost, n):
        lo, hi = stats.poisson_interval(n)
        return cost / hi, (cost / lo if lo > 0 else float("inf"))
    blo, bhi = band(b_cost, b_n)
    alo, ahi = band(a_cost, a_n)
    if alo > bhi or ahi < blo:
        side = "上" if alo > bhi else "下"
        return {"判定": f"偶然では説明しにくい差（{side}）",
                "幅（円）": {"前": [round(blo), round(bhi)], "後": [round(alo), round(ahi) if ahi != float("inf") else None]}}
    return {"判定": "差があるとは言えない",
            "幅（円）": {"前": [round(blo), round(bhi)], "後": [round(alo), round(ahi) if ahi != float("inf") else None]}}


def before_after(days: Dict[str, dict], before: List[date], after: List[date], confirmed_until: date) -> dict:
    """1 つの対象（合計、またはキャンペーン 1 本）の前後の表。成果の系は確定した日だけで比べる。"""
    nb, na = len(before), len(after)
    b, a = _sum(days, before), _sum(days, after)
    before_c = [d for d in before if d <= confirmed_until]
    after_c = [d for d in after if d <= confirmed_until]
    bc, ac = _sum(days, before_c), _sum(days, after_c)
    yen = convert.micros_to_yen
    rows = []

    def count_row(name, key, unit="件"):
        bv, av = b[key], a[key]
        rows.append({"指標": name, "前（合計）": bv, "後（合計）": av,
                     f"前（1 日あたり、{unit}）": round(bv / nb, 2), f"後（1 日あたり、{unit}）": round(av / na, 2),
                     "差（1 日あたり、%）": _pct_change(bv / nb, av / na), **count_test(int(bv), nb, int(av), na)})

    count_row("表示回数", "impressions", "回")
    count_row("クリック数", "clicks")
    rows.append({"指標": "費用（円）", "前（合計）": yen(b["cost_micros"]), "後（合計）": yen(a["cost_micros"]),
                 "前（1 日あたり、円）": yen(b["cost_micros"] / nb), "後（1 日あたり、円）": yen(a["cost_micros"] / na),
                 "差（1 日あたり、%）": _pct_change(b["cost_micros"] / nb, a["cost_micros"] / na),
                 "判定": "判定しない（金額は件数ではない。クリック数とクリック単価で見る）"})
    ctr_b = b["clicks"] / b["impressions"] * 100 if b["impressions"] else None
    ctr_a = a["clicks"] / a["impressions"] * 100 if a["impressions"] else None
    rows.append({"指標": "クリック率（%）", "前": None if ctr_b is None else round(ctr_b, 2),
                 "後": None if ctr_a is None else round(ctr_a, 2), "差（%）": _pct_change(ctr_b, ctr_a),
                 **rate_test(b["clicks"], b["impressions"], a["clicks"], a["impressions"])})
    cpc_b = b["cost_micros"] / b["clicks"] if b["clicks"] else None
    cpc_a = a["cost_micros"] / a["clicks"] if a["clicks"] else None
    rows.append({"指標": "平均クリック単価（円）", "前": None if cpc_b is None else yen(cpc_b),
                 "後": None if cpc_a is None else yen(cpc_a), "差（%）": _pct_change(cpc_b, cpc_a),
                 **unit_cost_test(b["cost_micros"] / 1e6, b["clicks"], a["cost_micros"] / 1e6, a["clicks"])})
    # 成果の系（確定した日だけ）
    conv_note = {"使った日数": {"前": len(before_c), "後": len(after_c)}}
    cb, ca = round(bc["conversions"]), round(ac["conversions"])
    if not before_c or not after_c:
        for name in ("コンバージョン数", "コンバージョン率（%）", "コンバージョン単価（円）"):
            rows.append({"指標": name, **conv_note,
                         **_undecidable("成果が確定した日（遅れの日数より前の日）が、前か後に 1 日も無い")})
    else:
        rows.append({"指標": "コンバージョン数", **conv_note, "前（合計）": round(bc["conversions"], 2),
                     "後（合計）": round(ac["conversions"], 2),
                     "前（1 日あたり）": round(bc["conversions"] / len(before_c), 3),
                     "後（1 日あたり）": round(ac["conversions"] / len(after_c), 3),
                     "差（1 日あたり、%）": _pct_change(bc["conversions"] / len(before_c), ac["conversions"] / len(after_c)),
                     **count_test(cb, len(before_c), ca, len(after_c))})
        cvr_b = bc["conversions"] / bc["clicks"] * 100 if bc["clicks"] else None
        cvr_a = ac["conversions"] / ac["clicks"] * 100 if ac["clicks"] else None
        rows.append({"指標": "コンバージョン率（%）", **conv_note, "前": None if cvr_b is None else round(cvr_b, 2),
                     "後": None if cvr_a is None else round(cvr_a, 2), "差（%）": _pct_change(cvr_b, cvr_a),
                     **rate_test(cb, bc["clicks"], ca, ac["clicks"])})
        cpa_b = bc["cost_micros"] / bc["conversions"] if bc["conversions"] else None
        cpa_a = ac["cost_micros"] / ac["conversions"] if ac["conversions"] else None
        rows.append({"指標": "コンバージョン単価（円）", **conv_note, "前": None if cpa_b is None else yen(cpa_b),
                     "後": None if cpa_a is None else yen(cpa_a), "差（%）": _pct_change(cpa_b, cpa_a),
                     **unit_cost_test(bc["cost_micros"] / 1e6, cb, ac["cost_micros"] / 1e6, ca)})
    return {"行": rows, "_cost": b["cost_micros"] + a["cost_micros"]}


def weekday_mix(before: List[date], after: List[date]) -> Tuple[dict, bool]:
    """前後の曜日の数。割合がそろっているか（どの曜日も、期間の日数に対する割合が同じか）。"""
    cb, ca = Counter(d.weekday() for d in before), Counter(d.weekday() for d in after)
    view = {WEEKDAY_JA[i]: {"前": cb.get(i, 0), "後": ca.get(i, 0)} for i in range(7)}
    even = all(abs(cb.get(i, 0) / len(before) - ca.get(i, 0) / len(after)) < 1e-9 for i in range(7))
    return view, even


def effect_basis() -> List[dict]:
    return [
        kb.cite_line("36", "変更日 ＋ 遅れ ＋ 評価の期間"),
        kb.cite_line("36", "約 2 週間で学習して最適化"),
        kb.cite_line("36", "曜日構成をそろえる"),
        kb.cite_line("36", "判定不能"),
        kb.cite_line("90", "ほかのモデルで約 15 時間"),
        kb.cite_line("90", "直近から除く日数"),
        kb.cite_line("36", "成果 10 件以上"),
    ]


# ================= いつもの水準からの外れ =================

def _kb_step(row_id: str, fallback: str) -> Tuple[str, dict]:
    """36 の日次の点検表から「異常だったら最初にすること」を引く。読めなければ道具の中の短い文。"""
    try:
        hit = kb.find_row("36", row_id)
    except kb.KBError:
        hit = None
    cite = kb.cite("36", row_id)
    if hit:
        _, u = hit
        for k, v in u["cells"].items():
            if "最初にすること" in k and v:
                return convert_text(v), cite
    return fallback, cite


def convert_text(text: str) -> str:
    import re

    return re.sub(r"[*`]", "", text)


def _finding(check: str, target: str, weight: str, summary: str, numbers: dict, step_row: str,
             fallback: str, tree: str = "") -> dict:
    assert weight in WEIGHT_ORDER
    step, cite = _kb_step(step_row, fallback)
    out = {"点検": check, "対象": target, "結果": "所見あり", "重さ": weight, "所見": summary,
           "根拠の数字": numbers, "確かめること": step}
    if tree:
        out["原因を切り分けるには"] = tree
    out["知識ベースの根拠"] = cite
    out["誰の意見"] = OPINION
    return out


def _unknown(check: str, target: str, reason: str) -> dict:
    return {"点検": check, "対象": target, "結果": "判定できない", "理由": reason}


def _ok(check: str, target: str) -> dict:
    return {"点検": check, "対象": target, "結果": "問題なし"}


def _zero_streak(days: Dict[str, dict], dates: List[date], key: str) -> int:
    n = 0
    for d in reversed(dates):
        if (days.get(d.isoformat()) or {}).get(key, 0):
            break
        n += 1
    return n


def detect(target: str, info: dict, days: Dict[str, dict], end: date, baseline: List[date],
           budget_checks: bool = True) -> List[dict]:
    """1 つの対象（キャンペーン 1 本、またはアカウント全体）の、見た日の外れ。"""
    out: List[dict] = []
    get = lambda d, k: (days.get(d.isoformat()) or {}).get(k, 0)  # noqa: E731
    prev = end - timedelta(days=1)
    served = [d for d in baseline if get(d, "impressions") > 0]
    if served:  # 配信を始める前の日は、比べる相手に入れない
        baseline = [d for d in baseline if d >= served[0]]
    baseline_ok = len(served) >= MIN_BASE_DAYS
    if not baseline_ok:
        out.append(_unknown("いつもの水準", target,
                            f"比べる期間に表示のあった日が {len(served)} 日しか無い（新しい、または止まっていた）。"
                            f"{MIN_BASE_DAYS} 日以上たまるまで、表示・クリック・率・成果の外れは判定しない（予算に対する費用だけ見る）"))
    same = [d for d in baseline if d.weekday() == end.weekday()]
    compare = same if len(same) >= SAME_WEEKDAY_MIN else baseline
    compare_label = (f"前 {len(same)} 週の同じ曜日（{WEEKDAY_JA[end.weekday()]}）の平均" if compare is same
                     else f"前 {len(baseline)} 日の平均")
    same_prev = [d for d in baseline if d.weekday() == prev.weekday() and d != prev]
    compare_prev = same_prev if len(same_prev) >= SAME_WEEKDAY_MIN else [d for d in baseline if d != prev]
    mean = lambda dates, k: sum(get(d, k) for d in dates) / len(dates) if dates else 0.0  # noqa: E731
    enabled = info.get("status", "ENABLED") == "ENABLED"

    # D1: 配信が止まっていないか
    imp_end, e_imp = get(end, "impressions"), mean(compare, "impressions")
    if baseline_ok and enabled and imp_end == 0:
        if e_imp == 0:
            out.append(_ok("D1 配信の停止", target))
        else:
            lo, _ = poisson_range(e_imp)
            if lo > 0:
                out.append(_finding("D1 配信の停止", target, "大", f"{end} の表示が 0 回（いつもは {e_imp:,.1f} 回）",
                                    {"表示回数": 0, "いつもの水準": round(e_imp, 1), "比べた相手": compare_label,
                                     "偶然で出る範囲の下限": lo},
                                    "D1", "ステータスの理由 → 支払い → 審査 → 終了日・スケジュール → 予算 → 入札の上限・目標の順に見る"))
            else:
                out.append(_unknown("D1 配信の停止", target,
                                    f"表示が 0 回だが、いつもの水準が {e_imp:,.1f} 回と少なく、0 回も偶然の範囲に入る"))
    # 表示・クリックの下振れ（向きは下だけ。36 の 5-6）
    if baseline_ok and imp_end > 0:
        for key, name, tree in (("impressions", "表示回数", "kb_logic_tree(metric='表示回数', direction='下')"),
                                ("clicks", "クリック数", "kb_logic_tree(metric='クリック数', direction='下')")):
            check = f"{name}の下振れ"
            e, v = mean(compare, key), get(end, key)
            if e < MIN_EXPECTED:
                out.append(_unknown(check, target, f"いつもの水準が 1 日 {e:,.1f} で {MIN_EXPECTED} に届かず、0 でも偶然の範囲に入りやすい"))
                continue
            lo, hi = poisson_range(e)
            if v >= lo:
                out.append(_ok(check, target))
                continue
            e_prev = mean(compare_prev, key)
            two_days = e_prev >= MIN_EXPECTED and get(prev, key) < poisson_range(e_prev)[0]
            out.append(_finding(
                check, target, "中" if two_days else "小",
                f"{end} の{name}が {v:,} で、いつもの {e:,.1f} から偶然の範囲（{lo:,}〜{hi:,}）より下"
                + ("。前日も下に外れていて 2 日続いている" if two_days else "。1 日だけなので、明日も外れたら調べる（2 日続いたら鳴らす目安）"),
                {name: v, "いつもの水準": round(e, 1), "比べた相手": compare_label, "偶然で出る範囲": [lo, hi],
                 "前日も外れたか": two_days},
                "D3", "D1 と同じ順に見る。表示が少ないなら表示回数の木、表示はあるのにクリックが無いなら広告の状態と掲載位置", tree))
    # クリック率の下振れ（率。2 つの率の検定）
    c_imp, c_clk = sum(get(d, "impressions") for d in compare), sum(get(d, "clicks") for d in compare)
    y_imp, y_clk = imp_end, get(end, "clicks")
    if baseline_ok and y_imp > 0:
        if c_imp == 0 or c_clk + y_clk < MIN_EVENTS:
            out.append(_unknown("クリック率の下振れ", target, f"比べる相手と見た日のクリックが合わせて {c_clk + y_clk} 件で、{MIN_EVENTS} 件に届かない"))
        else:
            t = stats.two_proportion_test(c_clk, c_imp, y_clk, y_imp)
            if t["p"] < CTR_ALPHA and y_clk / y_imp < c_clk / c_imp:
                out.append(_finding("クリック率の下振れ", target, "小",
                                    f"{end} のクリック率が {y_clk / y_imp * 100:.2f}% で、いつもの {c_clk / c_imp * 100:.2f}% より低い（p 値 {t['p']:.4f}）",
                                    {"クリック率（%）": round(y_clk / y_imp * 100, 2), "いつもの（%）": round(c_clk / c_imp * 100, 2),
                                     "比べた相手": compare_label, "p 値": round(t["p"], 6), "方法": t["方法"]},
                                    "D3", "広告の審査の状態、掲載位置、新しく出た検索語句を見る",
                                    "kb_logic_tree(metric='クリック率', direction='下')"))
            else:
                out.append(_ok("クリック率の下振れ", target))
    # D2・D3: 予算に対する費用（キャンペーン単位だけ。予算はいまの額）
    budget = info.get("budget_micros") or 0
    if budget_checks and budget > 0:
        c_end, c_prev = get(end, "cost_micros"), get(prev, "cost_micros")
        nums = {"見た日の費用（円）": convert.micros_to_yen(c_end), "前日の費用（円）": convert.micros_to_yen(c_prev),
                "1 日の予算（円、いまの額）": convert.micros_to_yen(budget)}
        if c_end > OVER_BUDGET * budget and c_prev > OVER_BUDGET * budget:
            out.append(_finding("D2 費用の使いすぎ", target, "大",
                                f"費用が 2 日続けて日予算の {OVER_BUDGET} 倍を超えた（{c_prev / budget:.2f} 倍、{c_end / budget:.2f} 倍）",
                                nums, "D2", "変更履歴で予算・入札・目標・拡大系の設定の変更を確かめる → 共有予算の切り替え当日か → クリックの急増なら無効なクリックを疑う",
                                "kb_logic_tree(metric='費用', direction='上')"))
        elif c_end > DAILY_CAP * budget:
            out.append(_finding("D2 費用の使いすぎ", target, "中",
                                f"{end} の費用が日予算の {c_end / budget:.2f} 倍で、1 日の上限（2 倍）を超えて見える。その日に予算を変えた・共有予算・予算の額が当時と違う、のどれかを確かめる",
                                nums, "D2", "変更履歴で予算の変更を確かめる"))
        else:
            out.append(_ok("D2 費用の使いすぎ", target))
        if enabled and baseline_ok:
            usual = mean(baseline, "cost_micros")
            if usual < UNDER_BUDGET * budget:
                out.append(_unknown("D3 費用の使えなさ", target,
                                    f"いつもの費用（1 日 {convert.micros_to_yen(usual):,} 円）がもともと日予算の {UNDER_BUDGET} 倍未満なので、比べられない"))
            elif c_end < UNDER_BUDGET * budget and c_prev < UNDER_BUDGET * budget:
                out.append(_finding("D3 費用の使えなさ", target, "中",
                                    f"費用が 2 日続けて日予算の {UNDER_BUDGET} 倍未満（いつもは 1 日 {convert.micros_to_yen(usual):,} 円）",
                                    {**nums, "いつもの費用（1 日、円）": convert.micros_to_yen(usual)},
                                    "D3", "D1 と同じ順に見る", "kb_logic_tree(metric='費用', direction='下')"))
            else:
                out.append(_ok("D3 費用の使えなさ", target))
    # D4: 成果の急変（0 件の連続、3 倍以上）
    total_conv = sum(get(d, "conversions") for d in baseline)
    lam = total_conv / len(baseline) if baseline else 0.0
    if not baseline_ok:
        pass
    elif total_conv < MIN_EVENTS:
        out.append(_unknown("D4 成果の急変", target, f"比べる期間の成果が {total_conv:,.1f} 件で、{MIN_EVENTS} 件に届かない"))
    else:
        need = math.ceil(3 / lam)
        streak = _zero_streak(days, baseline + [end], "conversions")
        y_conv = get(end, "conversions")
        if streak >= need:
            out.append(_finding("D4 成果の急変", target, "大",
                                f"成果 0 件が {streak} 日続いている（1 日の平均 {lam:.2f} 件なら、偶然で 5% 未満になるのは {need} 日）",
                                {"0 件の連続": streak, "1 日の平均": round(lam, 3), "偶然で 5% 未満になる日数": need},
                                "D4", "成果がどのアクションで落ちたかを見る → 計測の停止 → LP を実際に開く。入札は触らない",
                                "kb_logic_tree(metric='コンバージョン数', direction='下')"))
        elif y_conv >= CONV_SPIKE * lam and y_conv >= 3:
            out.append(_finding("D4 成果の急変", target, "中",
                                f"{end} の成果が {y_conv:,.1f} 件で、いつもの {lam:.2f} 件の {CONV_SPIKE} 倍以上（二重計上の疑い）",
                                {"成果": round(y_conv, 2), "1 日の平均": round(lam, 3)},
                                "D4", "成果がどのアクションで増えたかを見る（二重計上・タグの重複）",
                                "kb_logic_tree(metric='コンバージョン数', direction='上')"))
        else:
            out.append(_ok("D4 成果の急変", target))
    return out


def summarize(results: List[dict]) -> dict:
    findings = sorted((r for r in results if r["結果"] == "所見あり"), key=lambda r: (WEIGHT_ORDER[r["重さ"]], r["点検"]))
    unknown = [r for r in results if r["結果"] == "判定できない"]
    return {
        "まとめ": {"所見": {w: sum(1 for r in findings if r["重さ"] == w) for w in ("大", "中", "小")},
                 "判定できない": len(unknown), "問題なし": sum(1 for r in results if r["結果"] == "問題なし")},
        "所見": findings,
        "判定できなかったもの": unknown,
    }


def anomaly_basis() -> List[dict]:
    return [
        kb.cite("36", "D2", expect=("1.6",)),
        kb.cite_line("36", "前 4〜8 週の同じ曜日の平均比"),
        kb.cite_line("36", "最も単純なモデル（ポアソン分布"),
        kb.cite_line("36", "2 回続いたら鳴らす"),
        kb.cite_line("36", "費用は「上振れ」、表示・クリック・成果は「下振れ」"),
        kb.cite_line("36", "d ≒ 3 ÷ λ"),
    ]
