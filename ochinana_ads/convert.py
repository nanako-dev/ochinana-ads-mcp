"""数字と行の整形。API は呼ばない。

- 金額は円に直して返す（micros は外に出さない）
- 列名は日本語
- 割り算（クリック率・単価）はここで計算する
- 除外キーワードは普通のキーワードと別の欄に分ける
"""
from __future__ import annotations

import csv
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

RAW_METRICS = ("impressions", "clicks", "cost_micros", "conversions", "conversions_value")

STATUS_JA = {"ENABLED": "有効", "PAUSED": "一時停止", "REMOVED": "削除済み"}
MATCH_JA = {"EXACT": "完全一致", "PHRASE": "フレーズ一致", "BROAD": "部分一致"}
CHANNEL_JA = {
    "SEARCH": "検索",
    "PERFORMANCE_MAX": "P-MAX",
    "DISPLAY": "ディスプレイ",
    "DEMAND_GEN": "デマンド ジェネレーション",
    "VIDEO": "動画",
    "SHOPPING": "ショッピング",
    "MULTI_CHANNEL": "アプリ",
}
TERM_STATUS_JA = {
    "NONE": "なし",
    "ADDED": "キーワードに追加済み",
    "EXCLUDED": "除外済み",
    "ADDED_EXCLUDED": "追加と除外の両方",
}
SORT_KEYS = {"費用": "cost_micros", "クリック": "clicks", "表示": "impressions"}


def ja(table: Dict[str, str], name: str) -> str:
    """API の英語の値を日本語に。知らない値はそのまま返す（黙って消さない）。"""
    return table.get(name, name)


def micros_to_yen(micros) -> int:
    """micros（100 万分の 1）を円に。1 円未満は四捨五入。"""
    return int((Decimal(int(micros)) / Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_yen(value) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def require_jpy(currency: str) -> None:
    """円以外の通貨のアカウントは、為替の換算を持たないので断る。"""
    if currency != "JPY":
        raise ValueError(
            f"このアカウントの通貨は {currency}。円への為替換算は未対応なので、数字を返さない。"
        )


def metrics_ja(raw: dict) -> dict:
    """実績の数字を、日本語の列名と円で返す。割れないものは None。"""
    imp = int(raw.get("impressions", 0))
    clicks = int(raw.get("clicks", 0))
    cost_micros = int(raw.get("cost_micros", 0))
    conv = float(raw.get("conversions", 0.0))
    conv_value = float(raw.get("conversions_value", 0.0))
    return {
        "表示回数": imp,
        "クリック数": clicks,
        "費用（円）": micros_to_yen(cost_micros),
        "クリック率（%）": round(clicks / imp * 100, 2) if imp else None,
        "平均クリック単価（円）": micros_to_yen(cost_micros / clicks) if clicks else None,
        "コンバージョン数": round(conv, 2),
        "コンバージョン単価（円）": micros_to_yen(cost_micros / conv) if conv else None,
        "コンバージョン値（円）": round_yen(conv_value),
    }


def total(rows: Iterable[dict]) -> dict:
    t = {"impressions": 0, "clicks": 0, "cost_micros": 0, "conversions": 0.0, "conversions_value": 0.0}
    for r in rows:
        for k in RAW_METRICS:
            t[k] += r.get(k, 0)
    return t


def has_activity(row: dict) -> bool:
    return any(row.get(k, 0) for k in RAW_METRICS)


def to_ja_row(row: dict) -> dict:
    dims = {k: v for k, v in row.items() if k not in RAW_METRICS}
    return {**dims, **metrics_ja(row)}


def sort_rows(rows: List[dict], sort_by: str = "費用") -> List[dict]:
    if sort_by not in SORT_KEYS:
        raise ValueError("並び順は 費用 / クリック / 表示 のどれか")
    first = SORT_KEYS[sort_by]
    rest = [k for k in ("cost_micros", "clicks", "impressions") if k != first]
    return sorted(rows, key=lambda r: tuple(r.get(k, 0) for k in [first, *rest]), reverse=True)


def clamp_top_n(top_n, default: int, maximum: int) -> int:
    try:
        n = int(top_n)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


def report(rows: List[dict], top_n: int, sort_by: str = "費用") -> Tuple[dict, List[dict]]:
    """合計（全件）と上位 N 件を作る。実績が全部ゼロの行は省き、省いた数を返す。

    返り値: (返答に入れる部分, 実績のある全行を並べ替えたもの)
    """
    active = sort_rows([r for r in rows if has_activity(r)], sort_by)
    body = {
        "合計": metrics_ja(total(rows)),
        "件数": {
            "実績あり": len(active),
            "表示している件数": min(top_n, len(active)),
            "実績ゼロで省いた件数": len(rows) - len(active),
        },
        "並び順": f"{sort_by}の多い順",
        "行": [to_ja_row(r) for r in active[:top_n]],
    }
    return body, active


def split_keywords(rows: Iterable[dict]) -> Tuple[List[dict], List[dict]]:
    """キーワードを「普通」と「除外」に分ける。どの行にも negative の欄を必ず付ける。

    negative が欠けている行は、どちらか分からないので例外にする（黙って普通の側に入れない）。
    """
    normal: List[dict] = []
    negative: List[dict] = []
    for r in rows:
        if "negative" not in r or not isinstance(r["negative"], bool):
            raise ValueError(f"negative の欄が無い行がある: {r.get('キーワード')!r}")
        (negative if r["negative"] else normal).append(dict(r))
    return normal, negative


def count_by(rows: Iterable[dict], key: str) -> dict:
    return dict(Counter(r.get(key) for r in rows).most_common())


def pick_most_common(values: Iterable[str]) -> Optional[str]:
    """いちばん多く使われている指定を選ぶ。同数なら名前順で先のもの（毎回同じ結果にする）。"""
    c = Counter(v for v in values if v)
    if not c:
        return None
    best = max(c.values())
    return sorted(k for k, n in c.items() if n == best)[0]


def write_csv(path: Path, rows: List[dict]) -> str:
    """全件を CSV に保存し、保存先を返す。Excel でそのまま開けるよう BOM つき UTF-8。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return str(path)
