"""採算・予算のペース・一覧の突き合わせ。決まった計算だけで、API も外も呼ばない。

式は知識ベース（37 の 1-3、36 の 7-2・7-3、14 の 98〜102、L3 の 3-1、15 の 3-08・3-09）で確かめたものを使い、
根拠の行を呼ぶたびに引き直して返す。
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional

from . import kb
from .checks import blocked_by_negative

DAYS_PER_MONTH = 30.4  # 14 の 98: 1 か月の請求は最大で 1 日の平均予算の 30.4 倍
DAILY_CAP = 2          # 14 の 98: 1 日の請求は最大で 2 倍
PACE_BANDS = ((90, 110, "健全", "何もしない"), (80, 120, "注意", "今週中に補正"), (0, float("inf"), "危険", "当日中に補正"))


def _num(name: str, v, *, low: float = 0, allow_zero: bool = False, high: Optional[float] = None) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}は数字で渡す（受け取った値: {v!r}）") from None
    if x < low or (x == low and not allow_zero) or (high is not None and x > high):
        rng = f"{low} 以上" if allow_zero else f"{low} より大きい"
        if high is not None:
            rng += f"、{high} 以下"
        raise ValueError(f"{name}は {rng}の数で渡す（受け取った値: {v}）")
    return x


def _opt(name: str, v, **kw) -> Optional[float]:
    return None if v is None or v == "" else _num(name, v, **kw)


def _yen(x: Optional[float]) -> Optional[int]:
    return None if x is None else round(x)


# ================= 採算 =================

def unit_economics(sale_price_yen=None, gross_margin_rate=None, gross_profit_yen=None, purchases_per_customer=1,
                   ltv_months=None, spend_share=1.0, incremental_rate=1.0, close_rate=1.0,
                   cpc_yen=None, cpa_yen=None, cvr=None) -> dict:
    price = _opt("売上単価", sale_price_yen)
    margin = _opt("粗利率", gross_margin_rate, high=1)
    gp = _opt("1 件の粗利", gross_profit_yen)
    if gp is None:
        if price is None or margin is None:
            raise ValueError("gross_profit_yen（1 件の粗利）か、sale_price_yen（売上単価）と gross_margin_rate（粗利率 0〜1）を渡す")
        gp = price * margin
    elif margin is None and price:
        margin = gp / price
    times = _num("期間内の平均購入回数", purchases_per_customer)
    share = _num("獲得に使ってよい割合", spend_share, high=1)
    incr = _num("増分率", incremental_rate, high=1)
    close = _num("メインの成果から成約への率", close_rate, high=1)
    months = _opt("LTV の期間の月数", ltv_months)
    cpc, cpa, rate = _opt("クリック単価", cpc_yen), _opt("獲得単価", cpa_yen), _opt("コンバージョン率", cvr, high=1)

    ltv = gp * times
    be_sale = ltv * incr
    ok_sale = ltv * share * incr
    out: Dict[str, object] = {
        "対象アカウント": "なし（計算）",
        "期間": f"LTV の期間 {months:g} か月" if months else "LTV の期間は指定なし",
        "単位": "金額は円、率は 0〜1",
        "入力": {"売上単価": price, "粗利率": margin, "1 件の粗利": _yen(gp), "期間内の平均購入回数": times,
                "獲得に使ってよい割合": share, "増分率": incr, "メインの成果から成約への率": close,
                "クリック単価": cpc, "獲得単価（実績か予定）": cpa, "コンバージョン率": rate},
        "1 件の粗利": _yen(gp),
        "生涯の粗利（LTV）": _yen(ltv),
        "損益分岐の獲得単価": {"成約 1 件": _yen(be_sale), "メインの成果 1 件": _yen(be_sale * close),
                       "式": "LTV × 増分率（× メインの成果から成約への率）。粗利を全部広告に使うと利益 0"},
        "許容できる獲得単価": {"成約 1 件": _yen(ok_sale), "メインの成果 1 件": _yen(ok_sale * close),
                       "式": "LTV × 獲得に使ってよい割合 × 増分率（× メインの成果から成約への率）"},
    }
    roas = {}
    if margin:
        roas["初回の売上で値を付けるとき"] = {"倍": round(1 / margin / share, 2), "%": round(100 / margin / share, 1),
                                    "式": "1 ÷ 粗利率 ÷ 獲得に使ってよい割合"}
        if times != 1 and price:
            r = 1 / (ltv / price) / share
            roas["LTV で値を付けるとき"] = {"倍": round(r, 2), "%": round(r * 100, 1),
                                       "式": "1 ÷（LTV の粗利 ÷ 初回の売上）÷ 獲得に使ってよい割合"}
        if incr < 1:
            r = 1 / margin / share / incr
            roas["増分率も入れたとき（この道具の拡張）"] = {"倍": round(r, 2), "%": round(r * 100, 1),
                                               "式": "1 ÷ 粗利率 ÷ 獲得に使ってよい割合 ÷ 増分率。37 の式には増分率が入っていない"}
    out["目標広告費用対効果の下限"] = roas or "粗利率が分からないので出していない（sale_price_yen と gross_margin_rate を渡す）"
    if months:
        monthly = ltv / months
        pay = {"1 か月あたりの粗利（1 人）": _yen(monthly),
               "損益分岐の単価で獲得したとき（月）": round(be_sale / monthly, 1) if monthly else None,
               "許容の単価で獲得したとき（月）": round(ok_sale / monthly, 1) if monthly else None}
        if cpa:
            pay["渡した獲得単価のとき（月）"] = round(cpa / close / monthly, 1)
        pay["式"] = "成約 1 件にかかった費用（メインの成果の単価 ÷ 成約への率）÷ 1 か月あたりの粗利。購入が期間の中で均等に起きるとみなす。増分率は入れていない（記録された成約 1 件として数える）"
        out["回収期間"] = pay
    else:
        out["回収期間"] = "ltv_months（LTV を見た期間の月数）を渡すと出す"
    rev = {}
    target_cpa = cpa if cpa else ok_sale * close
    if cpc and target_cpa:
        rev["必要なコンバージョン率"] = {"値": round(cpc / target_cpa, 5), "%": round(cpc / target_cpa * 100, 3),
                               "使った獲得単価": _yen(target_cpa), "式": "クリック単価 ÷ 獲得単価"}
    if cpc and rate:
        rev["このクリック単価と率での獲得単価"] = {"値": _yen(cpc / rate), "式": "クリック単価 ÷ コンバージョン率"}
    if rate and target_cpa:
        rev["この率で払えるクリック単価の上限"] = {"値": _yen(target_cpa * rate), "使った獲得単価": _yen(target_cpa),
                                     "式": "獲得単価 × コンバージョン率"}
    if rev:
        out["逆算"] = rev
    notes = []
    if incr == 1:
        notes.append("増分率を 1（広告が無ければ全部起きなかった）としている。実験が無ければ仮置きと明記する（37 の 1-3 の手順 4）")
    if share == 1:
        notes.append("獲得に使ってよい割合を 1 にしているので、許容の単価＝損益分岐の単価。割合は広告主が決める（37 の 1-3 の手順 3）")
    notes.append("損益分岐は上限であって目標ではない。実績が無い立ち上げでは、この値を目標として入札に入れない（37 の 1-3）")
    out["注記"] = notes
    out["根拠"] = [kb.cite("37", "1. 1 件の粗利", expect=("粗利率",)), kb.cite("37", "2. 生涯の粗利（LTV）", expect=("平均購入回数",)),
                 kb.cite("37", "3. 獲得に使ってよい割合"), kb.cite("37", "4. 増分率で割り引く", expect=("増分率",)),
                 kb.cite("37", "5. メインの成果地点へ換算"), kb.cite_line("37", "目標 ROAS の下限"),
                 kb.cite_line("L3", "クリック単価 ÷ コンバージョン率")]
    return out


# ================= 予算のペース =================

def budget_pace(monthly_budget_yen, spent_yen, remaining_days, days_in_month=None, current_daily_budget_yen=None,
                month: str = "") -> dict:
    budget = _num("月の予算", monthly_budget_yen)
    spent = _num("今日までの費用", spent_yen, allow_zero=True)
    if days_in_month in (None, ""):
        if month:
            m = re.fullmatch(r"(\d{4})-(\d{2})", str(month).strip())
            if not m:
                raise ValueError("month は YYYY-MM で渡す")
            y, mo = int(m.group(1)), int(m.group(2))
        else:
            y, mo = date.today().year, date.today().month
        dim = calendar.monthrange(y, mo)[1]
        dim_src = f"{y}-{mo:02d} の日数"
    else:
        dim = int(_num("月の日数", days_in_month, high=31))
        dim_src = "渡された値"
    rem = _num("残り日数", remaining_days, allow_zero=True, high=dim)
    if rem != int(rem):
        raise ValueError("残り日数は整数で渡す")
    rem = int(rem)
    elapsed = dim - rem
    daily = _opt("いまの日予算", current_daily_budget_yen)
    out: Dict[str, object] = {
        "対象アカウント": "なし（計算）",
        "期間": f"{dim} 日の月（{dim_src}）。経過 {elapsed} 日・残り {rem} 日",
        "単位": "金額は円、ペースは %",
        "入力": {"月の予算": budget, "今日までの費用": spent, "残り日数": rem, "月の日数": dim, "いまの日予算": daily},
    }
    left = budget - spent
    if elapsed > 0:
        pace = spent / (budget * elapsed / dim) * 100
        band = next(b for b in PACE_BANDS if b[0] <= pace <= b[1])
        out["ペース（%）"] = round(pace, 1)
        out["判定の帯"] = {"判定": band[2], "動き": band[3],
                         "帯": "90〜110% 健全／80〜90%・110〜120% 注意／80% 未満・120% 超 危険（実務家の公開ツールの標準。公式の基準ではない）"}
        out["月末の着地の予測（いまのペースのまま）"] = _yen(spent / elapsed * dim)
    else:
        out["ペース（%）"] = None
        out["判定の帯"] = "まだ経過日数が 0 なので出さない"
    out["残りの予算"] = _yen(left)
    if rem > 0:
        new_daily = max(left, 0) / rem
        out["日予算の案"] = {
            "残りの日の目標（1 日あたり）": _yen(new_daily),
            "この日予算での 1 日の上限（2 倍）": _yen(new_daily * DAILY_CAP),
            "この日予算で残りの期間に使われうる上限": _yen(new_daily * rem),
            "式": "（月の予算 − ここまでの費用）÷ 月の残り暦日数。月の途中で変えると残りの上限は「新しい日予算 × 残り日数」になるので、ちょうど残りの予算に収まる",
        }
    out["基本の日予算（月の初めから置くなら）"] = {"値": _yen(budget / DAYS_PER_MONTH), "式": "月の予算 ÷ 30.4"}
    if daily:
        cap_month = daily * DAYS_PER_MONTH
        out["いまの日予算のままなら"] = {
            "1 日の上限（2 倍）": _yen(daily * DAILY_CAP),
            "月の上限（30.4 倍。月の初めから同じ日予算のとき）": _yen(cap_month),
            "月の予算との差": _yen(cap_month - budget),
            "読み方": "月の上限が月の予算を超えるなら、月の予算を超えて使われうる" if cap_month > budget * 1.001 else
                    "月の上限は月の予算以内",
        }
    if left < 0:
        out["注記_使いすぎ"] = f"すでに月の予算を {_yen(-left):,} 円超えている"
    out["注記"] = [
        "1 日だけ日予算の 2 倍近く使われるのは仕様の範囲。日別ではなく月の累計で見る（36 の 7-1）。",
        "予算で制限されていない（目標に届かない・表示が出ない）なら、日予算を上げても使われない。先に原因を分ける（36 の 7-3 の手順 2）。",
        "31 日の月は 30.4 × 日予算 ＜ 31 × 日予算。28 日の 2 月に 30.4 倍まで使われうるかは知識ベースで未確認（36 の 7-2 ❓）。",
        "月の途中で変えた当日が「残りの日数」に入るかは知識ベースで未確認（36 の 7-2 ❓）。",
        "コンバージョンに対するお支払い・合計予算額のキャンペーンは 1 日 2 倍の決まりが違う（14 の 104・107）。",
        "単純な割り算の予測。Google の予算レポートの予測のほうが曜日の配分も考えている（36 の 7-3）。",
    ]
    out["根拠"] = [kb.cite("14", "98", expect=("2 倍", "30.4")), kb.cite("14", "100", expect=("残り日数",)),
                 kb.cite("14", "102", expect=("30.4",)), kb.cite("36", "ふつう", expect=("30.4",)),
                 kb.cite("36", "月の途中で補正", expect=("残り暦日数",)), kb.cite("36", "ペース（%）"),
                 kb.cite("36", "月末の着地の予測"), kb.cite("36", "残りの日の目標"), kb.cite("36", "90〜110%")]
    return out


# ================= 一覧の突き合わせ =================

MATCH_JA = {"EXACT": "完全一致", "PHRASE": "フレーズ一致", "BROAD": "部分一致"}


def _norm_item(text: str, width: bool, space: bool, case: bool, match_marks: bool) -> str:
    t = str(text)
    if width:
        t = unicodedata.normalize("NFKC", t)
    if match_marks:
        t = re.sub(r'^\s*-?\s*[\["]?|[\]"]?\s*$', "", t)
    t = t.strip()
    t = re.sub(r"\s+", "" if space else " ", t)
    if case:
        t = t.casefold()
    return t


def parse_keyword(text: str) -> Dict[str, str]:
    """キーワードの書き方から一致方式を読む: [完全一致]、"フレーズ一致"、それ以外は部分一致。先頭の - は除外の印として外す。"""
    t = unicodedata.normalize("NFKC", str(text)).strip()
    if t.startswith("-"):
        t = t[1:].strip()
    if t.startswith("[") and t.endswith("]"):
        return {"text": t[1:-1].strip(), "match": "EXACT"}
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        return {"text": t[1:-1].strip(), "match": "PHRASE"}
    return {"text": t, "match": "BROAD"}


def _as_items(v, name: str) -> List[str]:
    if isinstance(v, str):
        v = re.split(r"[\n,、]", v)
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"{name}は文字列の並び（または改行・読点で区切った文字列）で渡す")
    return [str(x).strip() for x in v if str(x).strip()]


def list_compare(list_a, list_b, mode: str = "差分", ignore_width: bool = True, ignore_space: bool = True,
                 ignore_case: bool = True, ignore_match_marks: bool = False, label_a: str = "A", label_b: str = "B",
                 max_items: int = 100) -> dict:
    a, b = _as_items(list_a, "list_a"), _as_items(list_b, "list_b")
    try:
        max_items = max(1, min(int(max_items), 1000))
    except (TypeError, ValueError):
        max_items = 100
    m = kb.norm(mode)
    if m in ("差分", "diff", "比較"):
        return _diff(a, b, ignore_width, ignore_space, ignore_case, ignore_match_marks, label_a, label_b, max_items)
    if m in ("除外の点検", "除外", "negative", "negatives"):
        return _negative_check(a, b, max_items)
    raise ValueError("mode は 差分 か 除外の点検")


def _groups(items: List[str], key) -> Dict[str, List[str]]:
    g: Dict[str, List[str]] = {}
    for x in items:
        g.setdefault(key(x), []).append(x)
    return g


def _diff(a, b, w, s, c, mm, la, lb, max_items) -> dict:
    key = lambda x: _norm_item(x, w, s, c, mm)  # noqa: E731
    ga, gb = _groups(a, key), _groups(b, key)
    both = [k for k in ga if k in gb]
    only_a = [k for k in ga if k not in gb]
    only_b = [k for k in gb if k not in ga]

    def view(keys, g1, g2=None):
        rows = []
        for k in keys[:max_items]:
            row = {"そろえた形": k, f"{la} の表記" if g2 is not None else "表記": sorted(set(g1[k]))}
            if g2 is not None:
                row[f"{lb} の表記"] = sorted(set(g2[k]))
            rows.append(row)
        return rows

    dups_a = {k: v for k, v in ga.items() if len(v) > 1}
    dups_b = {k: v for k, v in gb.items() if len(v) > 1}
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "一覧の 1 項目",
        "そろえ方": {"全角半角": w, "空白": "無視" if s else "1 つにまとめる", "大文字小文字": c,
                  "一致方式の記号（[ ] \" -）": "無視" if mm else "区別する"},
        "件数": {la: len(a), lb: len(b), "両方にある": len(both), f"{la} だけ": len(only_a), f"{lb} だけ": len(only_b),
                f"{la} の中の重複": len(dups_a), f"{lb} の中の重複": len(dups_b)},
        "両方にある": view(both, ga, gb),
        f"{la} だけ": view(only_a, ga),
        f"{lb} だけ": view(only_b, gb),
        "それぞれの中の重複（そろえると同じになるもの）": {la: [sorted(v) for v in dups_a.values()][:max_items],
                                         lb: [sorted(v) for v in dups_b.values()][:max_items]},
        "注記": [f"各欄は {max_items} 件まで表示（件数は全部）。"] if max(len(only_a), len(only_b), len(both)) > max_items else [],
    }


def _negative_check(keywords: List[str], negatives: List[str], max_items: int) -> dict:
    kws = [parse_keyword(k) for k in keywords]
    negs = [parse_keyword(n) for n in negatives]
    hits = []
    for k, raw in zip(kws, keywords):
        for n, nraw in zip(negs, negatives):
            if n["text"] and blocked_by_negative(k["text"], n["text"], n["match"]):
                hits.append({"キーワード": raw, "キーワードの一致方式": MATCH_JA[k["match"]],
                             "止めている除外キーワード": nraw, "除外の一致方式": MATCH_JA[n["match"]],
                             "理由": {"EXACT": "除外（完全一致）の語と、キーワードの語が同じ並び",
                                    "PHRASE": "除外（フレーズ一致）の語が、同じ順番でキーワードに含まれる",
                                    "BROAD": "除外（部分一致）の語が全部、キーワードに含まれる（順番は問わない）"}[n["match"]]})
    blocked = {h["キーワード"] for h in hits}
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "キーワードと除外キーワードの組",
        "書き方": "[語] は完全一致、\"語\" はフレーズ一致、それ以外は部分一致として読んだ。先頭の - は外した",
        "件数": {"キーワード": len(keywords), "除外キーワード": len(negatives), "止められているキーワード": len(blocked),
                "組": len(hits)},
        "止められているキーワード": hits[:max_items],
        "判定の範囲": [
            "キーワードの文そのものを検索語句とみなして、除外で止まるかを見た。部分一致・フレーズ一致のキーワードが広がった先の検索の一部だけを止める除外は、ここでは分からない。",
            "語の区切りは空白で見た（日本語の語の区切りは Google と完全には同じでない）。除外は類似パターン（言い換え・表記ゆれ）に広がらない（15 の 3-08）ので、表記ゆれは別の語として扱った。",
        ],
        "根拠": [kb.cite("15", "3-09", expect=("語順",)), kb.cite("15", "3-08", expect=("類似パターン",))],
    }
