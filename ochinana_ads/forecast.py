"""配信量の見込み。式は「検索数 × 表示される割合 × クリック率 × クリック単価」。API は呼ばない。

- 割合・率・単価は自分の実績から取る。実績が足りないキーワードは、アカウント全体の値で代用し、そう明記する。
- 見込みは幅つき（低・中・高）。実績が少ないほど幅が広い。
- 見込みは log/forecasts.jsonl に記録し、あとで実績と並べて差を見る（forecast_vs_actual）。
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import convert
from .config import ROOT

DAYS_PER_MONTH = 30.4
MIN_IMPRESSIONS_OWN = 100     # これ以上の表示があれば、そのキーワード自身の割合・率を使う
MIN_CLICKS_OWN = 10           # これ以上のクリックがあれば、そのキーワード自身の単価を使う
CPC_WIDTH = ((100, 0.15), (30, 0.30), (0, 0.50))   # クリック数ごとの、単価の幅（±）の置き値
Z95 = 1.959964
MAX_KEYWORDS = 50
ENV_LOG_DIR = "OCHINANA_ADS_LOG_DIR"

SOURCE_OWN = "このキーワードの実績"
SOURCE_ACCOUNT = "アカウント全体の実績で代用"

FORMULA = ("月の表示 = 月間検索数 × 表示される割合。月のクリック = 月の表示 × クリック率。"
           "月の費用 = 月のクリック × クリック単価。")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text)).casefold()).strip()


def has_japanese(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ一-鿿]", str(text)))


def script_notes(keywords: List[str], language_code: Optional[str]) -> List[str]:
    """キーワードの文字と、検索数を取る言語が食い違っていないか。"""
    notes = []
    japanese = [k for k in keywords if has_japanese(k)]
    if japanese and language_code != "ja":
        notes.append(
            f"日本語のキーワード（{'、'.join(japanese[:3])} など {len(japanese)} 個）なのに、"
            f"検索数を取る言語が日本語ではない（{language_code or '未定'}）。検索数が実態と大きくずれるおそれがある。"
        )
    latin = [k for k in keywords if not has_japanese(k)]
    if latin and language_code == "ja":
        notes.append(f"英数字だけのキーワード（{'、'.join(latin[:3])}）は、日本語を使う人の検索として数えている。")
    return notes


def wilson(successes: float, total: float, z: float = Z95) -> Tuple[float, float]:
    """割合の幅（ウィルソンの方法、95%）。件数が少ないほど広くなる。"""
    if total <= 0:
        return 0.0, 1.0
    p = min(1.0, max(0.0, successes / total))
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def eligible(impressions: float, share: Optional[float]) -> Optional[float]:
    """表示できたはずの回数（表示 ÷ 表示される割合）。"""
    if not impressions or not share or share <= 0:
        return None
    return impressions / share


def combine(rows: List[dict]) -> dict:
    """実績の行をまとめる。表示される割合は、表示できたはずの回数で重みを付けて出し直す。"""
    imp = sum(r.get("impressions", 0) for r in rows)
    clicks = sum(r.get("clicks", 0) for r in rows)
    cost = sum(r.get("cost_micros", 0) for r in rows)
    pool, pooled_imp = 0.0, 0
    for r in rows:
        e = eligible(r.get("impressions", 0), r.get("search_is"))
        if e:
            pool += e
            pooled_imp += r["impressions"]
    return {"impressions": imp, "clicks": clicks, "cost_micros": cost,
            "eligible": pool or None, "share_impressions": pooled_imp}


def _cpc_width(clicks: int) -> float:
    for threshold, width in CPC_WIDTH:
        if clicks >= threshold:
            return width
    return CPC_WIDTH[-1][1]


def _share(stat: dict) -> Optional[dict]:
    if not stat.get("eligible"):
        return None
    low, high = wilson(stat["share_impressions"], stat["eligible"])
    return {"mid": stat["share_impressions"] / stat["eligible"], "low": low, "high": high}


def _ctr(stat: dict) -> Optional[dict]:
    if not stat.get("impressions"):
        return None
    low, high = wilson(stat["clicks"], stat["impressions"])
    return {"mid": stat["clicks"] / stat["impressions"], "low": low, "high": high}


def _cpc(stat: dict) -> Optional[dict]:
    if not stat.get("clicks"):
        return None
    mid = stat["cost_micros"] / stat["clicks"] / 1_000_000
    w = _cpc_width(stat["clicks"])
    return {"mid": mid, "low": mid * (1 - w), "high": mid * (1 + w)}


def pick_rates(own: Optional[dict], account: dict) -> dict:
    """キーワードごとに、使う割合・率・単価と、その出どころを決める。"""
    own = own or {"impressions": 0, "clicks": 0, "cost_micros": 0, "eligible": None, "share_impressions": 0}
    enough_imp = own["impressions"] >= MIN_IMPRESSIONS_OWN
    enough_clicks = own["clicks"] >= MIN_CLICKS_OWN
    out = {}
    for key, fn, enough in (("share", _share, enough_imp), ("ctr", _ctr, enough_imp), ("cpc", _cpc, enough_clicks)):
        mine = fn(own) if enough else None
        if mine:
            out[key] = {**mine, "source": SOURCE_OWN}
        else:
            theirs = fn(account)
            out[key] = {**theirs, "source": SOURCE_ACCOUNT} if theirs else None
    out["own"] = {"impressions": own["impressions"], "clicks": own["clicks"]}
    return out


def volume_range(avg: Optional[int], monthly: Optional[List[int]]) -> Optional[dict]:
    """月間検索数の幅。年平均と直近 3 か月の平均が違えば、その 2 つを幅の両端にする。"""
    if avg is None:
        return None
    recent = None
    if monthly:
        last = monthly[-3:]
        recent = sum(last) / len(last)
    low, high = (min(avg, recent), max(avg, recent)) if recent is not None else (avg, avg)
    return {"mid": float(avg), "low": float(low), "high": float(high),
            "recent3": round(recent) if recent is not None else None}


LEVELS = (("low", "低"), ("mid", "中"), ("high", "高"))
OPPOSITE = {"low": "high", "mid": "mid", "high": "low"}


def raw_projection(volume: Optional[dict], rates: dict) -> Optional[dict]:
    """予算を考えない月の見込みと、1 クリック・1 表示あたりの費用（低・中・高）。"""
    if not volume or not rates.get("share") or not rates.get("ctr") or not rates.get("cpc"):
        return None
    raw = {"imp": {}, "clicks": {}, "cost": {}, "cpc": {}, "cpi": {}}
    for level, _ in LEVELS:
        imp = volume[level] * rates["share"][level]
        clicks = imp * rates["ctr"][level]
        raw["imp"][level], raw["clicks"][level] = imp, clicks
        raw["cost"][level] = clicks * rates["cpc"][level]
        raw["cpc"][level] = rates["cpc"][level]
        raw["cpi"][level] = rates["cpc"][level] * rates["ctr"][level]
    return raw


def _afford(budget: float, unit_cost: float) -> float:
    return budget / unit_cost if unit_cost > 0 else math.inf


def add_up(raws: List[Optional[dict]], monthly_budget_yen: Optional[float] = None) -> Optional[dict]:
    """見込みを足し、予算があればそこで頭打ちにする（キーワード 1 個のときも同じ計算）。

    予算が効くとき、表示とクリックの「低」は単価が高い場合、「高」は単価が低い場合で決まる
    （同じ予算でも、単価が安ければ多く買える）。どの項目でも 低 ≤ 中 ≤ 高 になる。
    """
    rows = [r for r in raws if r]
    if not rows:
        return None
    budget = monthly_budget_yen if monthly_budget_yen and monthly_budget_yen > 0 else math.inf
    out = {"表示回数": {}, "クリック数": {}, "費用（円）": {}}
    total = {k: {lv: sum(r[k][lv] for r in rows) for lv, _ in LEVELS} for k in ("imp", "clicks", "cost")}
    for level, name in LEVELS:
        opp = OPPOSITE[level]
        if level == "mid":  # 中: いまの配分のまま、足りない分だけ一律に縮む
            cost = total["cost"]["mid"]
            scale = min(1.0, budget / cost) if cost > 0 else 1.0
            imp, clicks = total["imp"]["mid"] * scale, total["clicks"]["mid"] * scale
        else:
            pick = max if level == "low" else min
            imp = min(total["imp"][level], _afford(budget, pick(r["cpi"][opp] for r in rows)))
            clicks = min(total["clicks"][level], _afford(budget, pick(r["cpc"][opp] for r in rows)))
        out["表示回数"][name] = round(imp)
        out["クリック数"][name] = round(clicks, 1)
        out["費用（円）"][name] = round(min(total["cost"][level], budget))
    out["予算で頭打ち"] = total["cost"]["mid"] > budget
    return out


def project(volume: Optional[dict], rates: dict, monthly_budget_yen: Optional[float] = None) -> Optional[dict]:
    """1 キーワードの月の見込み（低・中・高）。"""
    return add_up([raw_projection(volume, rates)], monthly_budget_yen)


def sum_views(views: List[Optional[dict]]) -> Optional[dict]:
    """返答用の見込み（低・中・高）を、そのまま足す。照合のときに使う。"""
    rows = [v for v in views if v]
    if not rows:
        return None
    return {metric: {name: round(sum(v[metric][name] for v in rows), 1 if metric == "クリック数" else None)
                     for _, name in LEVELS} for metric in ("表示回数", "クリック数", "費用（円）")}


def rate_view(rates: dict) -> dict:
    """使った割合・率・単価を、返答用の形にする。"""
    def pct(r):
        return None if not r else {"値": round(r["mid"] * 100, 2), "幅": [round(r["low"] * 100, 2), round(r["high"] * 100, 2)],
                                   "出どころ": r["source"]}
    cpc = rates.get("cpc")
    return {
        "表示される割合（%）": pct(rates.get("share")),
        "クリック率（%）": pct(rates.get("ctr")),
        "クリック単価（円）": None if not cpc else {"値": round(cpc["mid"]), "幅": [round(cpc["low"]), round(cpc["high"])],
                                             "出どころ": cpc["source"]},
        "このキーワードの実績": {"表示回数": rates["own"]["impressions"], "クリック数": rates["own"]["clicks"]},
    }


# ---- 記録と照合 ----

def log_path() -> Path:
    folder = Path(os.environ.get(ENV_LOG_DIR) or ROOT / "log")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "forecasts.jsonl"


def record(customer_id: str, rows: List[dict], total: Optional[dict], history: str,
           daily_budget_yen: Optional[float], now: Optional[datetime] = None) -> dict:
    """見込みを 1 行足す。見込みが出たキーワードだけを残す。"""
    now = now or datetime.now().astimezone()
    entry = {
        "id": f"F-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        "日時": now.isoformat(timespec="seconds"),
        "アカウント": customer_id,
        "実績を取った期間": history,
        "1 日の予算（円）": daily_budget_yen,
        "合計の見込み（月）": total,
        "キーワード": [{"キーワード": r["キーワード"], "月間検索数": r["月間検索数"], "見込み（月）": r["見込み（月）"]}
                   for r in rows if r.get("見込み（月）")],
    }
    path = log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"id": entry["id"], "保存先": str(path)}


def read_records(customer_id: Optional[str] = None) -> List[dict]:
    path = log_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if customer_id is None or r.get("アカウント") == customer_id:
            rows.append(r)
    return rows


def days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _judge(actual: float, low: float, high: float) -> str:
    if actual < low:
        return "見込みの幅より少ない"
    if actual > high:
        return "見込みの幅より多い"
    return "見込みの幅の中"


def compare_one(projection: dict, actual: dict, days: int) -> dict:
    """月の見込みを期間の日数に直して、実績と並べる。"""
    scale = days / DAYS_PER_MONTH
    actual_ja = {"表示回数": actual.get("impressions", 0), "クリック数": actual.get("clicks", 0),
                 "費用（円）": convert.micros_to_yen(actual.get("cost_micros", 0))}
    out = {}
    for metric in ("表示回数", "クリック数", "費用（円）"):
        low, mid, high = (projection[metric][n] * scale for n in ("低", "中", "高"))
        a = actual_ja[metric]
        digits = 1 if metric == "クリック数" else None
        out[metric] = {
            "見込み（期間に換算）": {"低": round(low, digits), "中": round(mid, digits), "高": round(high, digits)},
            "実績": a,
            "差（実績 − 見込みの中）": round(a - mid, digits),
            "実績 ÷ 見込みの中": round(a / mid, 2) if mid > 0 else None,
            "判定": _judge(a, low, high),
        }
    return out


def compare(entry: dict, actual_by_keyword: Dict[str, dict], start: str, end: str) -> dict:
    """記録した見込み 1 件と、期間の実績を並べる。"""
    days = days_between(start, end)
    rows, total_actual, projections = [], {"impressions": 0, "clicks": 0, "cost_micros": 0}, []
    for k in entry.get("キーワード", []):
        actual = actual_by_keyword.get(normalize(k["キーワード"]))
        if actual is None:
            rows.append({"キーワード": k["キーワード"], "実績": None,
                         "理由": "この期間、このキーワードの実績が無い（登録されていないか、表示が 0）"})
            continue
        for key in total_actual:
            total_actual[key] += actual.get(key, 0)
        projections.append(k["見込み（月）"])
        rows.append({"キーワード": k["キーワード"], **compare_one(k["見込み（月）"], actual, days)})
    total = sum_views(projections)
    return {
        "日数": days,
        "合計": compare_one(total, total_actual, days) if total else None,
        "行": rows,
    }
