"""Google 広告 API への接続。読み取り（検索）だけを持つ。

接続と検索の 2 つだけを持つ、薄い窓口。
認証ファイルは環境変数が指す場所からライブラリが直接読む。このコードは中身を開かず、出力もしない。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .config import ENV_CREDENTIALS_PATH


class SetupError(RuntimeError):
    """設定が足りないとき。理由と直し方を日本語で持つ。"""


def load_client():
    path = os.environ.get(ENV_CREDENTIALS_PATH)
    if not path:
        raise SetupError(
            f"環境変数 {ENV_CREDENTIALS_PATH} が設定されていない。"
            "認証ファイル（google-ads.yaml）の場所を指定して起動し直す。"
        )
    if not Path(path).is_file():
        raise SetupError(f"認証ファイルが見つからない: {path}")
    from google.ads.googleads.client import GoogleAdsClient

    return GoogleAdsClient.load_from_storage(path)


def search(client, customer_id: str, query: str) -> Iterator:
    """GAQL を実行して行を順に返す（読み取りだけ）。"""
    svc = client.get_service("GoogleAdsService")
    for batch in svc.search_stream(customer_id=customer_id, query=query):
        for row in batch.results:
            yield row


def explain_api_error(ex: Exception) -> str:
    """API のエラーを短い日本語にする。認証情報は含めない。"""
    failure = getattr(ex, "failure", None)
    if failure is None:
        return f"Google 広告 API を呼べなかった（{type(ex).__name__}）"
    parts = []
    for e in list(failure.errors)[:3]:
        code = e.error_code.WhichOneof("error_code") if hasattr(e.error_code, "WhichOneof") else ""
        parts.append(f"{code}: {e.message}" if code else e.message)
    rid = getattr(ex, "request_id", "")
    return "Google 広告 API のエラー。" + " / ".join(parts) + (f"（request_id {rid}）" if rid else "")


MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
          "OCTOBER", "NOVEMBER", "DECEMBER")


def keyword_history(client, customer_id: str, keywords, geo_id: int, language_id: int) -> list:
    """キーワードプランナーの過去の検索数を読む（読むだけ。キーワードプランは作らない）。"""
    svc = client.get_service("KeywordPlanIdeaService")
    req = client.get_type("GenerateKeywordHistoricalMetricsRequest")
    req.customer_id = customer_id
    req.keywords.extend(list(keywords))
    req.geo_target_constants.append(f"geoTargetConstants/{int(geo_id)}")
    req.language = f"languageConstants/{int(language_id)}"
    req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
    out = []
    for r in svc.generate_keyword_historical_metrics(request=req).results:
        m = r.keyword_metrics
        monthly = []
        for v in m.monthly_search_volumes:
            name = getattr(v.month, "name", str(v.month))
            monthly.append({"year": int(v.year), "month": MONTHS.index(name) + 1 if name in MONTHS else 0,
                            "searches": int(v.monthly_searches)})
        monthly.sort(key=lambda x: (x["year"], x["month"]))
        out.append({
            "text": r.text, "close_variants": list(r.close_variants),
            "avg_monthly_searches": int(m.avg_monthly_searches),
            "monthly": [x["searches"] for x in monthly],
            "low_bid_micros": int(m.low_top_of_page_bid_micros), "high_bid_micros": int(m.high_top_of_page_bid_micros),
        })
    return out


def api_version() -> str:
    """この道具が使っている Google 広告 API の版（ライブラリの既定）。"""
    import google.ads.googleads.client as gc

    return str(getattr(gc, "_DEFAULT_VERSION", "不明"))
