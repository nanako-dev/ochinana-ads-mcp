"""Meta 広告（Marketing API の Insights）を読むだけの部品。

守り（Google と同じ考え）:
- 呼ぶのは GET だけ。POST・DELETE を送る関数はこのファイルに無い（テストで確かめる）。
- 読んでよい広告アカウントは環境変数 ADS_ALLOWED_META_ACCOUNT_IDS（カンマ区切り。act_ は付けても付けなくてもよい）。
  リストに無い ID は、API を呼ぶ前に断る。ID はコードに書かない。
- 鍵（アクセストークン）は環境変数 META_ADS_TOKEN_FILE が指すファイルに本人が置く。この部品は呼ぶ直前にだけ読み、
  Authorization: Bearer ヘッダーで送る（URL に付けない）。Graph API の一般の書き方（2026-11-25 公式で確認）。
  Marketing API の頁はどれも access_token= の例だけなので、最初の本物の呼び出しで通るかを確かめる（未確認）。記録・返り値・例外の文・CSV には出さない。
- ページ送りは、応答の paging.next の URL を使わず（鍵が URL に入りうるため）、cursors.after を付けて自分で呼び直す。
- 呼び出しの上限のヘッダーを見て、どれかが 80% を超えたら待つ。

列の意味・窓・計上日は 媒体仕様メモ/Meta広告の基礎/65_APIとレポートの取り出し.md（§3・§4）。
"""
from __future__ import annotations

import json
import os
import re
import stat as _stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ENV_TOKEN_FILE = "META_ADS_TOKEN_FILE"
ENV_ALLOWED = "ADS_ALLOWED_META_ACCOUNT_IDS"
ENV_API_VERSION = "META_ADS_API_VERSION"
DEFAULT_API_VERSION = "v27.0"  # 2026-11-04 の最新（65 §3-1）。アプリの設定で自動更新されることがある
BASE = "https://graph.facebook.com"
ROOT = Path(__file__).resolve().parent.parent

LEVELS = ("campaign", "adset", "ad")
LEVEL_JA = {"campaign": "キャンペーン", "adset": "広告セット", "ad": "広告"}
INSIGHT_FIELDS = ("account_currency", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
                  "objective", "optimization_goal", "date_start", "date_stop", "impressions", "reach", "clicks",
                  "inline_link_clicks", "inline_link_click_ctr", "cost_per_inline_link_click", "cpm", "spend",
                  "results", "cost_per_result")
ACCOUNT_FIELDS = "account_id,name,currency,timezone_name,account_status"
ACCOUNT_STATUS_JA = {1: "有効", 2: "停止", 3: "未払い", 7: "審査待ち（リスク）", 8: "清算待ち", 9: "猶予期間",
                     100: "閉鎖待ち", 101: "閉鎖", 201: "有効（任意）", 202: "閉鎖（任意）"}
PAGE_LIMIT = 500
MAX_PAGES = 40
THROTTLE_PCT = 80
MAX_WAIT_SECONDS = 60

NOTES = {
    "結果": "「結果」の数え方は、キャンペーンの目的と広告セットの設定で決まる（トラフィックならリンククリック、売上なら購入など）。"
            "数えている物が違う行どうしを足さない",
    "窓": "成果のアトリビューションの窓は、広告セットのアトリビューション設定に従う（API の既定）",
    "計上日": "計上日は mixed: Meta の中の操作（リンクのクリックなど）は表示の日、Meta の外の成果（ウェブの購入など）は成果の日に付く",
    "まだ動く": "Insights は 15 分ごとに更新され、計上から 28 日たつまでは数字が変わりうる。直近 28 日の数字は確定ではない",
    "クリック": "クリック(すべて) には、いいね！・コメント・シェア・プロフィールのクリックも入る。Google のクリックと比べるならリンククリックを使う",
    "リーチ": "リーチは推定値で、人の重なりがあるので日や行を足し上げない",
    "結果の読み方": "results・cost_per_result は公式の型が「AdsInsightsResult の配列」で、中の形（indicator・values）の例は公式に無い（未確認）。"
                  "読めない形だったときは「結果」を空にして返す",
}


class MetaError(RuntimeError):
    """利用者に見せてよい文だけを持つ（鍵・URL は入れない）。"""


Fetch = Callable[[str, Dict[str, str]], Tuple[int, Dict[str, str], bytes]]


# ---------------- 設定（許可リスト・鍵のファイル） ----------------

def normalize_account_id(v: str) -> str:
    s = str(v or "").strip()
    s = s[4:] if s.lower().startswith("act_") else s
    if not re.fullmatch(r"\d{5,20}", s):
        raise ValueError(f"Meta の広告アカウント ID は数字（act_ は付けても付けなくてもよい）。受け取った値: {v!r}")
    return s


def allowed_ids() -> Tuple[str, ...]:
    raw = os.environ.get(ENV_ALLOWED, "")
    out = []
    for x in re.split(r"[,\s、]+", raw):
        if x.strip():
            out.append(normalize_account_id(x))
    return tuple(dict.fromkeys(out))


def require_allowed(account_id: str) -> str:
    aid = normalize_account_id(account_id)
    allowed = allowed_ids()
    if not allowed:
        raise ValueError(f"Meta の許可リストが空。本人が端末で `ochinana-ads-connect check --media meta` を動かし、"
                         f"出た 1 行（{ENV_ALLOWED}=…）を AI のアプリの設定に足す")
    if aid not in allowed:
        raise ValueError(f"広告アカウント {aid} は Meta の許可リストに無いので読まない（API は呼んでいない）。"
                         f"読む物は list_accounts ではなく meta_list_accounts で確かめる")
    return aid


def token_file_status() -> dict:
    """鍵のファイルの確かめ（中身は開かない。有無・大きさ・権限・置き場所だけ）。"""
    path = os.environ.get(ENV_TOKEN_FILE)
    if not path:
        return {"設定": "無い", "直し方": f"Meta のアクセストークン（ads_read の権限だけ）を 1 行だけ書いたファイルを作り、"
                                      f"chmod 600 にして、環境変数 {ENV_TOKEN_FILE} にその場所を入れて、AI のアプリを起動し直す"}
    p = Path(path).expanduser()
    if not p.is_file():
        return {"設定": "ある", "ファイル": "見つからない", "場所": str(p), "直し方": "場所を確かめる"}
    st = p.stat()
    out = {"設定": "ある", "ファイル": "ある", "場所": str(p), "大きさ": f"{st.st_size} バイト"}
    warn = []
    if st.st_mode & (_stat.S_IRGRP | _stat.S_IROTH):
        warn.append("ほかのユーザーも読める権限になっている。chmod 600 で本人だけにする")
    if ROOT in p.resolve().parents:
        warn.append("この道具のフォルダの中にある。誤って公開しないよう、フォルダの外へ移す")
    if warn:
        out["注意"] = warn
    return out


def _read_token() -> str:
    """呼ぶ直前にだけ読む。1 行の文字列か、{"access_token": "..."} の JSON。値は返すだけで、どこにも書かない。"""
    st = token_file_status()
    if st.get("ファイル") != "ある":
        raise MetaError("Meta の鍵のファイルが無い。" + st.get("直し方", ""))
    text = Path(os.environ[ENV_TOKEN_FILE]).expanduser().read_text(encoding="utf-8").strip()
    if text.startswith("{"):
        try:
            text = str(json.loads(text).get("access_token", "")).strip()
        except ValueError:
            raise MetaError("鍵のファイルの JSON が読めない（中身は表示しない）") from None
    elif "=" in text and "\n" not in text and not text.startswith("EA"):
        text = text.split("=", 1)[1].strip()
    if not text or "\n" in text or " " in text:
        raise MetaError("鍵のファイルの形が違う。アクセストークンだけを 1 行で書く（中身は表示しない）")
    return text


# ---------------- 呼び出し（GET だけ） ----------------

def _http_get(url: str, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:  # noqa: S310  読む先は graph.facebook.com だけ
            return res.status, {k.lower(): v for k, v in res.headers.items()}, res.read()
    except urllib.error.HTTPError as ex:
        return ex.code, {k.lower(): v for k, v in (ex.headers or {}).items()}, ex.read() or b""


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "[鍵]") if token else text


def usage_pct(headers: Dict[str, str]) -> Dict[str, float]:
    """上限のヘッダーから、使った割合（%）を拾う。読めないヘッダーは飛ばす。"""
    h = {k.lower(): v for k, v in headers.items()}
    out: Dict[str, float] = {}

    def load(name):
        try:
            return json.loads(h[name]) if name in h else None
        except ValueError:
            return None

    t = load("x-fb-ads-insights-throttle") or {}
    for k in ("app_id_util_pct", "acc_id_util_pct"):
        if isinstance(t.get(k), (int, float)):
            out[f"Insights の {k}"] = float(t[k])
    a = load("x-ad-account-usage") or {}
    if isinstance(a.get("acc_id_util_pct"), (int, float)):
        out["広告アカウントの点数"] = float(a["acc_id_util_pct"])
    for name, label in (("x-business-use-case-usage", "BUC"), ("x-app-usage", "アプリ")):
        v = load(name)
        items = []
        if isinstance(v, dict) and name == "x-app-usage":
            items = [v]
        elif isinstance(v, dict):
            for lst in v.values():
                items += [x for x in (lst if isinstance(lst, list) else [lst]) if isinstance(x, dict)]
        for x in items:
            for k in ("call_count", "total_cputime", "total_time"):
                if isinstance(x.get(k), (int, float)):
                    out[f"{label} の {k}"] = max(out.get(f"{label} の {k}", 0.0), float(x[k]))
    return out


def _regain_minutes(headers: Dict[str, str]) -> float:
    h = {k.lower(): v for k, v in headers.items()}
    best = 0.0
    for name in ("x-business-use-case-usage",):
        try:
            v = json.loads(h.get(name, "") or "{}")
        except ValueError:
            continue
        for lst in (v.values() if isinstance(v, dict) else []):
            for x in (lst if isinstance(lst, list) else []):
                if isinstance(x, dict) and isinstance(x.get("estimated_time_to_regain_access"), (int, float)):
                    best = max(best, float(x["estimated_time_to_regain_access"]))
    return best


class Client:
    """GET だけを持つ。fetch と sleep は、テストで差し替える。"""

    def __init__(self, fetch: Optional[Fetch] = None, sleep: Callable[[float], None] = time.sleep):
        self.fetch = fetch or _http_get
        self.sleep = sleep
        self.version = os.environ.get(ENV_API_VERSION, DEFAULT_API_VERSION)
        self.usage: Dict[str, float] = {}
        self.waited = 0.0

    def get(self, path: str, params: Optional[Dict[str, str]] = None) -> dict:
        params = dict(params or {})
        if "access_token" in params:
            raise ValueError("鍵は URL に付けない（ヘッダーで送る）")
        if not re.fullmatch(r"/[A-Za-z0-9_/]+", path):
            raise ValueError(f"呼ぶ先の形が違う: {path!r}")
        url = f"{BASE}/{self.version}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        token = _read_token()
        try:
            status, headers, body = self.fetch(url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
        except Exception as ex:  # 通信の失敗。文には鍵を入れない
            raise MetaError(f"Meta の API につながらなかった（{type(ex).__name__}）") from None
        self.usage = usage_pct(headers)
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            raise MetaError(f"Meta の API の応答が読めなかった（HTTP {status}）") from None
        if status >= 400 or "error" in data:
            raise MetaError(explain_error(data.get("error") or {}, status, token))
        high = max(self.usage.values(), default=0.0)
        if high >= THROTTLE_PCT:
            wait = min(MAX_WAIT_SECONDS, max(10.0, _regain_minutes(headers) * 60))
            self.sleep(wait)
            self.waited += wait
        return data

    def get_all(self, path: str, params: Dict[str, str]) -> List[dict]:
        """ページを cursors.after で送って全部読む（paging.next の URL は使わない）。"""
        rows: List[dict] = []
        p = dict(params)
        for _ in range(MAX_PAGES):
            data = self.get(path, p)
            rows += list(data.get("data") or [])
            after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (data.get("paging") or {}).get("next"):
                return rows
            p["after"] = after
        raise MetaError(f"行が多すぎる（{MAX_PAGES} ページで止めた）。期間を短くするか、level を上げる")


ERROR_HINTS = {
    190: "鍵（アクセストークン）が無効か期限切れ。Meta のビジネス設定でシステムユーザーのトークンを作り直し、鍵のファイルを書き換える",
    200: "権限が足りない。トークンに ads_read が付いているか、システムユーザーにこの広告アカウントが割り当てられているかを確かめる",
    10: "権限が足りない。トークンに ads_read が付いているかを確かめる",
    17: "呼び出しの上限に達した。数分あけてから呼ぶ",
    4: "アプリの呼び出しの上限に達した。数分あけてから呼ぶ",
    80000: "広告アカウントの呼び出しの上限に達した。数分あけてから呼ぶ",
    80004: "広告アカウントの呼び出しの上限に達した。数分あけてから呼ぶ",
    100: "引数が違うか、1 回で読む量が多すぎる。期間を短くするか、level を上げる",
    102: "ログインの状態が切れた。トークンを作り直す",
    341: "アプリの呼び出しの上限に達した。数分あけてから呼ぶ",
    613: "呼び出しの上限に達した。数分あけてから呼ぶ",
}
SUBCODE_HINTS = {
    1487534: "1 回で読む量が多すぎる。期間を短くするか、level を上げる",
    1504018: "時間切れ。期間を短くするか、level を上げる",
    1504022: "Insights の呼び出しの上限に達した。数分あけてから呼ぶ",
    1504039: "Insights の呼び出しの上限に達した。数分あけてから呼ぶ",
    3191001: "この広告アカウントの Insights を読む権限が無い。システムユーザーへの割り当てを確かめる",
}


def explain_error(err: dict, status: int, token: str = "") -> str:
    code = err.get("code")
    sub = err.get("error_subcode")
    msg = _scrub(str(err.get("message") or ""), token)[:300]
    hint = SUBCODE_HINTS.get(sub) or ERROR_HINTS.get(code, "")
    if not hint and isinstance(code, int) and 200 <= code <= 299:
        hint = ERROR_HINTS[200]
    head = f"Meta の API のエラー（HTTP {status}、code {code}" + (f"、subcode {sub}" if sub else "") + "）"
    trace = f"（fbtrace_id {err['fbtrace_id']}）" if err.get("fbtrace_id") else ""
    return f"{head}: {msg}{trace}" + (f" 直し方: {hint}" if hint else "")


# ---------------- 値の読み方 ----------------

def num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def result_value(v) -> Tuple[Optional[float], str]:
    """results・cost_per_result は、数のことも、[{"indicator": "...", "values": [{"value": "12"}]}] の形のこともある。
    返すのは（値, 何を数えたか）。種類が 2 つ以上なら値は None にして足さない。"""
    if isinstance(v, (int, float, str)):
        return num(v), ""
    if isinstance(v, list) and v:
        kinds = []
        total = 0.0
        found = False
        for item in v:
            if not isinstance(item, dict):
                continue
            kinds.append(str(item.get("indicator") or item.get("action_type") or ""))
            vals = item.get("values")
            if isinstance(vals, list):
                for x in vals:
                    n = num(x.get("value") if isinstance(x, dict) else x)
                    if n is not None:
                        total += n
                        found = True
            else:
                n = num(item.get("value"))
                if n is not None:
                    total += n
                    found = True
        kinds = [k for k in dict.fromkeys(kinds) if k]
        if len(kinds) > 1:
            return None, "・".join(kinds)
        return (total if found else None), (kinds[0] if kinds else "")
    return None, ""


# ---------------- 道具の中身 ----------------

def _client(client: Optional[Client]) -> Client:
    return client or Client()


def _money(v, currency: str) -> Optional[float]:
    n = num(v)
    if n is None:
        return None
    return round(n) if currency == "JPY" else round(n, 2)


def _ratio(a: float, b: float, scale: float = 1.0, nd: int = 2) -> Optional[float]:
    return round(a / b * scale, nd) if b else None


def _row(r: dict, level: str, daily: bool = False) -> dict:
    cur = r.get("account_currency") or ""
    d: dict = {}
    if daily:
        d["日付"] = r.get("date_start")
    d["キャンペーン"] = r.get("campaign_name")
    if level in ("adset", "ad"):
        d["広告セット"] = r.get("adset_name")
    if level == "ad":
        d["広告"] = r.get("ad_name")
    res, kind = result_value(r.get("results"))
    cpr, _ = result_value(r.get("cost_per_result"))
    d.update({
        "目的": r.get("objective"), "最適化": r.get("optimization_goal"),
        "表示": int(num(r.get("impressions")) or 0), "リーチ": int(num(r.get("reach")) or 0),
        "クリック(すべて)": int(num(r.get("clicks")) or 0), "リンククリック": int(num(r.get("inline_link_clicks")) or 0),
        "CTR(リンク)（%）": None if num(r.get("inline_link_click_ctr")) is None else round(num(r.get("inline_link_click_ctr")), 2),
        "CPC(リンク)": _money(r.get("cost_per_inline_link_click"), cur), "CPM": _money(r.get("cpm"), cur),
        "消化金額": _money(r.get("spend"), cur), "結果": res, "結果の種類": kind or None, "結果の単価": _money(cpr, cur),
        "通貨": cur or None,
    })
    return d


def _totals(rows: List[dict], currency: str) -> dict:
    imp = sum(r["表示"] for r in rows)
    clicks = sum(r["クリック(すべて)"] for r in rows)
    link = sum(r["リンククリック"] for r in rows)
    spend = sum(r["消化金額"] or 0 for r in rows)
    kinds = {r["結果の種類"] for r in rows if r["結果"] is not None}
    out = {"表示": imp, "クリック(すべて)": clicks, "リンククリック": link,
           "CTR(リンク)（%）": _ratio(link, imp, 100), "CPC(リンク)": _money(spend / link, currency) if link else None,
           "CPM": _money(spend / imp * 1000, currency) if imp else None, "消化金額": _money(spend, currency)}
    if len(kinds) == 1 and None not in kinds:
        res = sum(r["結果"] for r in rows if r["結果"] is not None)
        out.update({"結果": round(res, 2), "結果の種類": next(iter(kinds)), "結果の単価": _money(spend / res, currency) if res else None})
    elif kinds:
        out["結果"] = "足さない（数えている物が行ごとに違う: " + "・".join(sorted(k or "不明" for k in kinds)) + "）"
    return out


def _currency_note(currency: str) -> Optional[str]:
    if currency and currency != "JPY":
        return f"このアカウントの通貨は {currency}。金額は円ではなく {currency} のまま（換算していない）"
    return None


def _head(aid: str, start: str, end: str, unit: str) -> dict:
    return {"対象アカウント": f"Meta 広告 {aid}", "期間": f"{start}〜{end}（広告アカウントのタイムゾーンの日付）", "単位": unit}


def _limits(c: Client) -> dict:
    out = {"使った割合（%）": c.usage or "ヘッダーが無かった"}
    if c.waited:
        out["待った秒数"] = c.waited
    return out


def _insights(c: Client, aid: str, start: str, end: str, level: str, daily: bool) -> List[dict]:
    params = {"level": level, "time_range": json.dumps({"since": start, "until": end}),
              "fields": ",".join(INSIGHT_FIELDS), "limit": str(PAGE_LIMIT)}
    if daily:
        params["time_increment"] = "1"
    return c.get_all(f"/act_{aid}/insights", params)


def _check_level(level: str) -> str:
    if level not in LEVELS:
        raise ValueError(f"level は {' / '.join(LEVELS)} のどれか（受け取った値: {level!r}）")
    return level


def list_accounts(client: Optional[Client] = None) -> dict:
    ids = allowed_ids()
    if not ids:
        raise ValueError(f"Meta の許可リストが空。本人が端末で `ochinana-ads-connect check --media meta` を動かし、"
                         f"出た 1 行（{ENV_ALLOWED}=…）を AI のアプリの設定に足す")
    c = _client(client)
    rows = []
    for aid in ids:
        try:
            a = c.get(f"/act_{aid}", {"fields": ACCOUNT_FIELDS})
            r = {"ID": aid, "名前": a.get("name"), "通貨": a.get("currency"), "タイムゾーン": a.get("timezone_name"),
                 "状態": ACCOUNT_STATUS_JA.get(a.get("account_status"), f"不明（{a.get('account_status')}）")}
            note = _currency_note(a.get("currency") or "")
            if note:
                r["注意"] = note
        except MetaError as ex:
            r = {"ID": aid, "読めない理由": str(ex)}
        rows.append(r)
    return {"対象アカウント": f"Meta の許可リストの {len(ids)} 件", "期間": "指定なし（いまの設定）", "単位": "広告アカウント",
            "アカウント": rows, "呼び出しの上限": _limits(c)}


def performance(account_id: str, start: str, end: str, level: str = "campaign", top_n: int = 20,
                client: Optional[Client] = None, out_dir: Optional[Path] = None) -> dict:
    from . import convert, gaql
    from .config import out_dir as _out

    aid = require_allowed(account_id)
    gaql.validate_period(start, end)
    _check_level(level)
    top_n = convert.clamp_top_n(top_n, 20, 100)
    c = _client(client)
    raw = _insights(c, aid, start, end, level, daily=False)
    currency = next((r.get("account_currency") for r in raw if r.get("account_currency")), "")
    rows = sorted((_row(r, level) for r in raw), key=lambda r: -(r["消化金額"] or 0))
    out = {**_head(aid, start, end, f"{LEVEL_JA[level]} 1 つごとの期間合計。金額は {currency or '通貨不明'}"),
           "件数": {"行": len(rows), "表示した行": min(top_n, len(rows))},
           "合計": _totals(rows, currency) if rows else "この期間に配信が無い"}
    out["行"] = rows[:top_n] if rows else "まだ無い（この期間に配信が無い）"
    if len(rows) > top_n:
        path = (out_dir or _out()) / f"meta_performance_{aid}_{level}_{start}_{end}.csv"
        out["全件の CSV"] = convert.write_csv(path, rows)
    note = _currency_note(currency)
    out["注記"] = {**NOTES, **({"通貨": note} if note else {})}
    out["呼び出しの上限"] = _limits(c)
    return out


def daily(account_id: str, start: str, end: str, level: str = "campaign", client: Optional[Client] = None,
          out_dir: Optional[Path] = None) -> dict:
    from . import convert, gaql
    from .config import out_dir as _out

    aid = require_allowed(account_id)
    gaql.validate_period(start, end)
    _check_level(level)
    c = _client(client)
    raw = _insights(c, aid, start, end, level, daily=True)
    currency = next((r.get("account_currency") for r in raw if r.get("account_currency")), "")
    rows = sorted((_row(r, level, daily=True) for r in raw), key=lambda r: (r["日付"] or "", -(r["消化金額"] or 0)))
    by_day: Dict[str, List[dict]] = {}
    for r in rows:
        by_day.setdefault(r["日付"], []).append(r)
    days = [{"日付": d, **{k: v for k, v in _totals(rs, currency).items()}} for d, rs in sorted(by_day.items())]
    out = {**_head(aid, start, end, f"1 日 × {LEVEL_JA[level]} 1 つごと。下の表は 1 日ごとの合計（リーチは足し上げないので出さない）。"
                                    f"金額は {currency or '通貨不明'}"),
           "件数": {"行": len(rows), "日": len(days)}}
    out["日別の合計"] = days or "まだ無い（この期間に配信が無い）"
    if rows:
        path = (out_dir or _out()) / f"meta_daily_{aid}_{level}_{start}_{end}.csv"
        out["全件の CSV"] = convert.write_csv(path, rows)
    note = _currency_note(currency)
    out["注記"] = {**NOTES, **({"通貨": note} if note else {})}
    out["呼び出しの上限"] = _limits(c)
    return out


def check(client: Optional[Client] = None) -> dict:
    """check_connection の Meta の部分。鍵のファイル → 許可リスト → 各アカウント、の順に見る。"""
    steps: List[dict] = []
    f = token_file_status()
    steps.append({"段": "1. 鍵のファイル", "結果": "よい" if f.get("ファイル") == "ある" else "まだ", **f})
    ids = allowed_ids()
    steps.append({"段": "2. 許可リスト", "結果": "よい" if ids else "まだ",
                  **({"件数": f"{len(ids)} 件"} if ids else {"直し方": f"本人が端末で `ochinana-ads-connect check --media meta` を動かし、"
                                                                     f"読む広告アカウントを選んで出た 1 行（{ENV_ALLOWED}=…）を設定に足す"})})
    if f.get("ファイル") != "ある" or not ids:
        return {"段": steps, "結論": "Meta はまだつないでいない（使わないなら、このままでよい）。用意するもの: "
                                   "①Meta のビジネスポートフォリオ ②開発者のアプリ ③ads_read だけのシステムユーザーのトークン "
                                   "④その鍵のファイルと環境変数 ⑤許可リスト。手順は README の「Meta 広告を読む」"}
    acc = list_accounts(client)
    ok = all("読めない理由" not in r for r in acc["アカウント"])
    steps.append({"段": "3. 許可リストの広告アカウント", "結果": "よい" if ok else "直す", "アカウント": acc["アカウント"]})
    return {"段": steps, "結論": "Meta もつながった。許可リストの広告アカウントを読める。" if ok else "読めない広告アカウントがある。行の理由を見る。"}


def discover(client: Optional[Client] = None) -> List[dict]:
    """このトークンで読める広告アカウントの一覧（端末の connect check だけで使う。AI の道具からは呼ばない）。"""
    c = _client(client)
    rows = c.get_all("/me/adaccounts", {"fields": ACCOUNT_FIELDS, "limit": "100"})
    return [{"ID": normalize_account_id(r.get("account_id") or r.get("id", "")), "名前": r.get("name") or "",
             "通貨": r.get("currency") or "", "状態": ACCOUNT_STATUS_JA.get(r.get("account_status"), "")} for r in rows]
