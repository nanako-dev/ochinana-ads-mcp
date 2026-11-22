"""Google 広告 API への接続。読み取り（検索）だけを持つ。

接続と検索の 2 つだけを持つ、薄い窓口。
認証ファイルは環境変数が指す場所からライブラリが直接読む。このコードは中身を開かず、出力もしない。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, List, Tuple

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


def error_codes(ex: Exception) -> List[Tuple[str, str, str]]:
    """API のエラーから (種類, 名前, 文) を取り出す（例: ("authorization_error", "USER_PERMISSION_DENIED", "...")）。"""
    failure = getattr(ex, "failure", None)
    out: List[Tuple[str, str, str]] = []
    if failure is None:
        return out
    for e in list(getattr(failure, "errors", []))[:5]:
        ec = getattr(e, "error_code", None)
        field, name = "", ""
        try:
            pb = type(ec).pb(ec)  # proto-plus の形
        except Exception:
            pb = ec
        try:
            field = pb.WhichOneof("error_code") or ""
        except Exception:
            field = ""
        if field:
            v = getattr(ec, field, None)
            name = getattr(v, "name", "") or str(v or "")
        out.append((field, name, str(getattr(e, "message", "") or "")))
    return out


# よくある失敗と直し方（2026-11-22、Google 広告 API の公式の Developer token・Access levels・OAuth のページで確かめた）
_HINTS = {
    "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION": "Google Cloud のプロジェクトの利用権限が Test のまま（テスト用のアカウントしか読めない）。"
        "Cloud の「Google Ads API の概要」のページで Explorer を申請する。無料トライアルのプロジェクトや、請求が止まっているプロジェクトは断られる",
    "USER_PERMISSION_DENIED": "このログインにアカウントを読む権限が無い。マネージャー（MCC）経由で読むなら、"
        "認証ファイルに login_customer_id（マネージャーの 10 桁）を書く",
    "CUSTOMER_NOT_ENABLED": "アカウントが有効でない（停止・解約・作りかけ）。管理画面で状態を確かめる",
    "DEVELOPER_TOKEN_PROHIBITED": "開発者トークンは 2026-09-09 に廃止された。認証ファイルの developer_token の行を消す",
    "OAUTH_TOKEN_EXPIRED": "認証の期限が切れた。connect token で取り直す",
    "OAUTH_TOKEN_REVOKED": "認証が取り消された。connect token で取り直す",
    "OAUTH_TOKEN_INVALID": "認証ファイルの値が正しくない。connect token で取り直す",
    "RESOURCE_EXHAUSTED": "1 日の操作の上限に達した（Explorer は本番のアカウントへ 2,880 操作、Basic は 15,000 操作。24 時間の幅で数える）",
    "RESOURCE_TEMPORARILY_EXHAUSTED": "短い時間に呼びすぎた。少し待ってから呼び直す",
}
_KNOWN_ISSUE = ("2026-09-09 より後に利用権限を上げたプロジェクトで、本番のアカウントだけ AUTHORIZATION_ERROR になる既知の問題がある"
                "（テスト用のアカウントは読める）。公式の回避策は、新しい Cloud プロジェクトで Explorer を申請し直すこと")


def hint_for(ex: Exception, keyword_planner: bool = False) -> str:
    """失敗の直し方を 1〜2 文で。分からなければ空。認証情報は含めない。"""
    codes = error_codes(ex)
    hints = [_HINTS[n] for _, n, _ in codes if n in _HINTS]
    fields = {f for f, _, _ in codes}
    if keyword_planner and "authorization_error" in fields:
        hints.insert(0, "キーワードプランナー（forecast_volume）は、利用権限が Basic 以上のプロジェクトでだけ動く（Explorer では使えない）。"
                        "Basic はブランドの確認のあとに申請する")
    if not hints and "authorization_error" in fields:
        hints.append(_KNOWN_ISSUE)
    text = str(ex)
    if not codes and ("invalid_grant" in text or type(ex).__name__ == "RefreshError"):
        hints.append("リフレッシュトークンが切れたか、取り消された。OAuth の同意画面が「テスト中」だと 7 日で切れるので、"
                     "「本番」にしてから connect token で取り直す")
    return " ".join(dict.fromkeys(hints))


def explain_api_error(ex: Exception, keyword_planner: bool = False) -> str:
    """API のエラーを短い日本語にする。認証情報は含めない。"""
    failure = getattr(ex, "failure", None)
    if failure is None:
        return f"Google 広告 API を呼べなかった（{type(ex).__name__}）"
    parts = [f"{n or f}: {m}" if (n or f) else m for f, n, m in error_codes(ex)[:3]]
    rid = getattr(ex, "request_id", "")
    out = "Google 広告 API のエラー。" + " / ".join(parts) + (f"（request_id {rid}）" if rid else "")
    h = hint_for(ex, keyword_planner)
    return out + (f" 直し方: {h}" if h else "")


def explain_auth_error(ex: Exception) -> str:
    """google.auth の失敗（リフレッシュトークンの期限切れなど）。中身の値は出さない。"""
    h = hint_for(ex)
    return f"Google の認証に失敗した（{type(ex).__name__}）。" + (h if h else "認証ファイルを connect token で作り直すと直ることが多い。")


def accessible_customer_ids(client) -> List[str]:
    """このログインで直接読めるアカウントの ID（マネージャーの配下は含まない）。名前は読まない。"""
    svc = client.get_service("CustomerService")
    res = svc.list_accessible_customers()
    return [r.split("/")[-1] for r in res.resource_names]


def credentials_file_status() -> dict:
    """認証ファイルの場所の確かめ（中身は開かない。有無・大きさ・権限・置き場所だけ）。"""
    import stat as _stat

    path = os.environ.get(ENV_CREDENTIALS_PATH)
    if not path:
        return {"設定": "無い", "直し方": f"環境変数 {ENV_CREDENTIALS_PATH} に認証ファイルの場所を入れて、AI のアプリを起動し直す"}
    p = Path(path).expanduser()
    if not p.is_file():
        return {"設定": "ある", "ファイル": "見つからない", "場所": str(p), "直し方": "場所を確かめる（connect token で作れる）"}
    st = p.stat()
    out = {"設定": "ある", "ファイル": "ある", "場所": str(p), "大きさ": f"{st.st_size} バイト"}
    warn = []
    if st.st_mode & (_stat.S_IRGRP | _stat.S_IROTH):
        warn.append("ほかのユーザーも読める権限になっている。chmod 600 で本人だけにする")
    root = Path(__file__).resolve().parent.parent
    if root in p.resolve().parents:
        warn.append("この道具のフォルダの中にある。誤って公開しないよう、フォルダの外へ移す")
    if warn:
        out["注意"] = warn
    return out


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
