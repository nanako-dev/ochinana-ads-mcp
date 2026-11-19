"""業種平均（ベンチマーク）を読む。ファイルは運用サイトと共有の 媒体仕様メモ/_ベンチマーク/benchmarks.json（読むだけ）。

中身は米国の中央値（WordStream・LocaliQ、第三者）。日本の業種別の信頼できる数字は見つかっていない（2026-11-19 の調べ）。
"""
from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Optional

ENV_FILE = "ADS_BENCHMARK_FILE"
DEFAULT_FILE = Path(__file__).resolve().parents[1] / "kb" / "_ベンチマーク" / "benchmarks.json"  # 同梱。ADS_BENCHMARK_FILE で差し替えられる
DEFAULT_SET = {"google": "localiq_google_search_2026", "facebook": "wordstream_facebook_leads_2025"}
SET_ALIASES = {"google": "google", "検索": "google", "google広告": "google", "search": "google",
               "facebook": "facebook", "meta": "facebook", "meta広告": "facebook", "fb": "facebook"}
DEFAULT_USD_JPY = 150.0


def _n(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).casefold().replace(" ", "").replace("・", "").replace("/", "").replace("&", "")


def load(path: Optional[str] = None) -> dict:
    p = Path(path or os.environ.get(ENV_FILE) or DEFAULT_FILE)
    if not p.is_file():
        raise ValueError(f"業種平均のファイルが見つからない: {p}（環境変数 {ENV_FILE} で場所を指せる）")
    return json.loads(p.read_text(encoding="utf-8"))


def lookup(industry: str, media: str = "google", set_id: str = "", usd_jpy: Optional[float] = None) -> dict:
    d = load()
    sets = {s["id"]: s for s in d["sets"]}
    sid = set_id or DEFAULT_SET.get(SET_ALIASES.get(_n(media), _n(media)), "")
    if sid not in sets:
        raise ValueError(f"業種平均の組は {' / '.join(sets)} のどれか（media は google か facebook）")
    s = sets[sid]
    q = _n(industry)
    hits = [r for r in s["rows"] if q and (q in _n(r["industry_ja"]) or q in _n(r["industry_en"]))]
    if not hits:
        raise ValueError(f"業種「{industry}」が {sid} に無い。使える業種: {'、'.join(r['industry_ja'] for r in s['rows'])}")
    if len(hits) > 1 and not any(_n(r["industry_ja"]) == q or _n(r["industry_en"]) == q for r in hits):
        raise ValueError(f"業種「{industry}」に当たるものが複数ある: {'、'.join(r['industry_ja'] for r in hits)}")
    r = next((h for h in hits if _n(h["industry_ja"]) == q or _n(h["industry_en"]) == q), hits[0])
    rate = float(usd_jpy) if usd_jpy else DEFAULT_USD_JPY
    return {"業種": r["industry_ja"], "業種（英語）": r["industry_en"], "組": sid, "媒体": s["media"], "地域": s["region"],
            "期間": s["period"], "出典": s.get("source_title"), "URL": s["source_url"], "取得日": d.get("retrieved"),
            "ctr": r.get("ctr"), "cvr": r.get("cvr"),
            "cpc_yen": round(r["cpc_usd"] * rate) if r.get("cpc_usd") is not None else None,
            "cpl_yen": round(r["cpl_usd"] * rate) if r.get("cpl_usd") is not None else None,
            "換算": f"1 ドル {rate:g} 円" + ("（仮置き。usd_jpy で変えられる）" if not usd_jpy else "（渡された値）"),
            "注意": "米国の中央値（第三者の集計）。日本の実勢とは差がある。手元に数字が無いときの出発点にだけ使い、実績が出たら置きかえる。"
                    "日本の業種別の信頼できる平均は見つかっていない（日本の単価の水準はキーワードプランナーの入札単価の幅と自社の実績で見る）"}
