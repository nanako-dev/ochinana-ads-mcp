"""LP の監査。スマホの表示速度と、ページの基本の要素・タグの有無。

表示速度は、まず PageSpeed Insights（Google の公開の計測、鍵なし）で測る。断られたときは、
手元に入っている Lighthouse で測る。どちらで測ったかを返答に入れる。両方だめなら「測れなかった」と返す。
外へ送るのは URL だけ。生の結果は out/lp/ に保存し、返答には要点だけを入れる。

手元の Lighthouse は、すでに入っているものだけを使う。このコードが勝手に取りに行って入れることはしない
（入れ方は README）。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .config import ROOT, out_dir

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
USER_AGENT = "ads-lp-check/0.2"
PSI_TIMEOUT = 120
PAGE_TIMEOUT = 20
MAX_HTML_BYTES = 3_000_000
TOP_BLOCKING = 5

LIGHTHOUSE_TIMEOUT = 240
LIGHTHOUSE_VERSION = "13.6.0"            # README の入れ方で指定している版
ENV_LIGHTHOUSE_BIN = "OCHINANA_LIGHTHOUSE_BIN"
PSI_PAUSE_SECONDS = 3600                 # 上限で断られたら、この間は PageSpeed Insights を呼ばない
METHOD_PSI = "PageSpeed Insights"
METHOD_LOCAL = "手元の Lighthouse"

HttpGet = Callable[[str, int], Tuple[int, str, bytes]]
Lighthouse = Callable[[str, Path], dict]

_psi_paused_until = 0.0


class LpError(ValueError):
    """監査できなかった理由を日本語で持つ。"""


def check_url(url: str) -> str:
    """http/https の公開ページだけ受ける。ID やパスワード入りの URL、手元のアドレスは断る。"""
    u = urllib.parse.urlsplit(str(url).strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise LpError(f"URL は http:// か https:// で始まる形で指定する（受け取った値: {url!r}）")
    if u.username or u.password:
        raise LpError("ID やパスワードが入った URL は外へ送らない")
    host = u.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise LpError("手元や社内のアドレスは監査しない")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise LpError("手元や社内のアドレスは監査しない")
    return urllib.parse.urlunsplit(u)


def http_get(url: str, timeout: int) -> Tuple[int, str, bytes]:
    """1 回だけ取りに行く。返り値は (状態コード, 最後にたどり着いた URL, 中身)。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.geturl(), res.read(MAX_HTML_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, url, e.read(20_000)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LpError(f"つながらなかった（{type(e).__name__}: {getattr(e, 'reason', e)}）") from None


# ---- 表示速度 ----

def psi_request_url(url: str) -> str:
    """計測の依頼に付けるのは、対象の URL・スマホ指定・性能の区分だけ。"""
    q = urllib.parse.urlencode({"url": url, "strategy": "mobile", "category": "performance"})
    return f"{PSI_ENDPOINT}?{q}"


def _seconds(audits: dict, key: str) -> Optional[float]:
    v = (audits.get(key) or {}).get("numericValue")
    return round(v / 1000, 1) if isinstance(v, (int, float)) else None


def _short_url(u: str, limit: int = 90) -> str:
    u = str(u).split("?")[0]
    return u if len(u) <= limit else u[: limit - 1] + "…"


def blocking_resources(audits: dict, top: int = TOP_BLOCKING) -> List[dict]:
    """描画を止めている資源。Lighthouse の版で項目名が違うので両方を見る。"""
    for key in ("render-blocking-resources", "render-blocking-insight"):
        items = ((audits.get(key) or {}).get("details") or {}).get("items") or []
        rows = []
        for it in items:
            if not isinstance(it, dict) or not it.get("url"):
                continue
            rows.append({
                "資源": _short_url(it["url"]),
                "止めている時間（ミリ秒）": round(it.get("wastedMs") or 0),
                "大きさ（KB）": round((it.get("totalBytes") or 0) / 1024, 1),
            })
        if rows:
            rows.sort(key=lambda r: r["止めている時間（ミリ秒）"], reverse=True)
            return rows[:top]
    return []


def summarize_psi(raw: dict) -> dict:
    """PageSpeed Insights の生の結果から、要点だけを取り出す。"""
    lh = raw.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    score = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    if score is None and not audits:
        raise LpError("計測の結果に点数が入っていなかった")
    tbt = (audits.get("total-blocking-time") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    return {
        "総合点（100 点満点）": round(score * 100) if isinstance(score, (int, float)) else None,
        "最初の表示（秒）": _seconds(audits, "first-contentful-paint"),
        "メイン画像の表示（秒）": _seconds(audits, "largest-contentful-paint"),
        "操作を受け付けない時間の合計（ミリ秒）": round(tbt) if isinstance(tbt, (int, float)) else None,
        "レイアウトのずれ": round(cls, 3) if isinstance(cls, (int, float)) else None,
        "描画を止めている資源（上位）": blocking_resources(audits),
        "計測の条件": f"スマホ・Lighthouse {lh.get('lighthouseVersion', '不明')}・1 回の計測（測るたびに数点ぶれる）",
    }


# ---- 手元の Lighthouse ----

def find_lighthouse() -> Optional[str]:
    """手元に入っている Lighthouse を探す。環境変数の指定、このフォルダの node_modules、PATH の順。"""
    env = os.environ.get(ENV_LIGHTHOUSE_BIN)
    if env:
        return env if Path(env).is_file() else None
    local = ROOT / "node_modules" / ".bin" / "lighthouse"
    if local.is_file():
        return str(local)
    return shutil.which("lighthouse")


def lighthouse_command(binary: str, url: str, out_path: Path) -> List[str]:
    """スマホの条件で、性能の区分だけを測る。Chrome は画面を出さずに動かす。"""
    return [binary, url, "--only-categories=performance", "--form-factor=mobile", "--output=json",
            f"--output-path={out_path}", "--quiet", "--chrome-flags=--headless=new"]


def run_lighthouse(url: str, out_path: Path) -> dict:
    """手元の Lighthouse で 1 回測り、結果（PageSpeed Insights と同じ形に包んだもの）を返す。"""
    binary = find_lighthouse()
    if not binary:
        raise LpError("手元に Lighthouse が入っていない（入れ方は README の「LP の表示速度」）")
    env = dict(os.environ)
    node = shutil.which("node")
    extra = [str(Path(node).parent)] if node else []
    env["PATH"] = os.pathsep.join(extra + [str(Path.home() / ".local" / "bin"), env.get("PATH", "")])
    try:
        done = subprocess.run(lighthouse_command(binary, url, out_path), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=LIGHTHOUSE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise LpError(f"手元の Lighthouse が {LIGHTHOUSE_TIMEOUT} 秒で終わらなかった") from None
    except OSError as e:
        raise LpError(f"手元の Lighthouse を起動できなかった（{type(e).__name__}）") from None
    if done.returncode != 0 or not out_path.is_file():
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["理由は不明"]
        raise LpError(f"手元の Lighthouse が失敗した（終了コード {done.returncode}: {tail[0][:160]}）")
    try:
        result = json.loads(out_path.read_text(encoding="utf-8"))
    except ValueError:
        raise LpError("手元の Lighthouse の結果を読めなかった") from None
    error = result.get("runtimeError") or {}
    if error.get("code") and error.get("code") != "NO_ERROR":
        raise LpError(f"手元の Lighthouse がページを測れなかった（{error.get('code')}）")
    return {"lighthouseResult": result}


# ---- ページの要素とタグ ----

class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: Optional[str] = None
        self.description: Optional[str] = None
        self.canonical: Optional[str] = None
        self.lang: Optional[str] = None
        self.h1: List[str] = []
        self.scripts: List[str] = []
        self._in: Optional[str] = None
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "meta" and a.get("name", "").lower() == "description" and self.description is None:
            self.description = a.get("content", "").strip()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href", "").strip()
        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        elif tag in ("title", "h1") and self._in is None:
            self._in, self._buf = tag, []

    def handle_endtag(self, tag):
        if tag == self._in:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title" and self.title is None:
                self.title = text
            elif tag == "h1":
                self.h1.append(text)
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)


GOOGLE_ID = re.compile(r"\b(G-[A-Z0-9]{6,}|AW-\d{6,}|GTM-[A-Z0-9]{4,})\b")


def parse_page(html: str) -> dict:
    """title・meta description・h1・canonical と、Google タグの有無。"""
    p = _Head()
    try:
        p.feed(html)
    except Exception:  # 壊れた HTML でも、読めたところまでで返す
        pass
    signs = []
    if "googletagmanager.com/gtag/js" in html:
        signs.append("gtag.js の読み込み")
    if "googletagmanager.com/gtm.js" in html or "googletagmanager.com/ns.html" in html:
        signs.append("タグ マネージャーの読み込み")
    if re.search(r"\bgtag\s*\(", html):
        signs.append("gtag() の呼び出し")
    ids = sorted(set(GOOGLE_ID.findall(html)))
    return {
        "title": p.title,
        "title の文字数": len(p.title) if p.title else 0,
        "meta description": p.description,
        "meta description の文字数": len(p.description) if p.description else 0,
        "h1": p.h1[:3],
        "h1 の数": len(p.h1),
        "canonical": p.canonical,
        "言語の指定": p.lang,
        "Google タグ": {"有無": bool(signs or ids), "見つかった印": signs, "ID": ids},
    }


# ---- まとめて実行 ----

def _slug(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    s = re.sub(r"[^A-Za-z0-9]+", "_", f"{u.hostname}{u.path}").strip("_")
    return s[:80] or "page"


def _saved_speed(url: str, max_age_hours: float) -> Optional[Tuple[dict, Path, str]]:
    """少し前に測った結果があれば使う。返り値は (生の結果, ファイル, 測り方)。"""
    if max_age_hours <= 0:
        return None
    folder = out_dir() / "lp"
    files = sorted(list(folder.glob(f"psi_{_slug(url)}_*.json")) + list(folder.glob(f"lighthouse_{_slug(url)}_*.json")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:
        if time.time() - f.stat().st_mtime <= max_age_hours * 3600:
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if f.name.startswith("lighthouse_"):
                return {"lighthouseResult": raw}, f, METHOD_LOCAL
            return raw, f, METHOD_PSI
    return None


def _measure_psi(url: str, get: HttpGet, path: Path) -> dict:
    global _psi_paused_until
    if time.time() < _psi_paused_until:
        raise LpError("少し前に 1 日の上限で断られたので、今回は呼んでいない")
    status, _, body = get(psi_request_url(url), PSI_TIMEOUT)
    if status != 200:
        hint = ""
        if status == 429:
            _psi_paused_until = time.time() + PSI_PAUSE_SECONDS
            hint = "（鍵なしで使える共有の枠が、1 日の上限に達している）"
        raise LpError(f"HTTP {status} を返した{hint}")
    raw = json.loads(body.decode("utf-8", "replace"))
    speed = summarize_psi(raw)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return speed


def measure_speed(url: str, get: HttpGet, lighthouse: Lighthouse, folder: Path, stamp: str,
                  max_age_hours: float) -> Tuple[dict, Optional[Path]]:
    """表示速度を測る。PageSpeed Insights → 手元の Lighthouse の順に試し、どちらで測ったかを入れて返す。"""
    cached = _saved_speed(url, max_age_hours)
    if cached:
        raw, path, method = cached
        try:
            speed = summarize_psi(raw)
            speed["計測の条件"] += f"・{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M} に測った結果を再利用"
            return {"測れたか": True, "測り方": method, **speed}, path
        except LpError:
            pass
    psi_path = folder / f"psi_{_slug(url)}_{stamp}.json"
    try:
        speed = _measure_psi(url, get, psi_path)
        return {"測れたか": True, "測り方": METHOD_PSI, **speed}, psi_path
    except (LpError, ValueError) as e:
        psi_reason = str(e)
    local_path = folder / f"lighthouse_{_slug(url)}_{stamp}.json"
    try:
        speed = summarize_psi(lighthouse(url, local_path))
        speed["計測の条件"] += "・この Mac の回線と性能で測った値（PageSpeed Insights の値とは少し違う）"
        return {"測れたか": True, "測り方": METHOD_LOCAL,
                "PageSpeed Insights を使わなかった理由": psi_reason, **speed}, local_path
    except (LpError, ValueError) as e:
        return {"測れたか": False,
                "理由": f"{METHOD_PSI}: {psi_reason}。{METHOD_LOCAL}: {e}"}, None


def audit(url: str, get: HttpGet = http_get, max_age_hours: float = 0,
          lighthouse: Lighthouse = run_lighthouse) -> dict:
    """LP を 1 本監査する。速度とページの要素は別々に取り、片方が失敗してももう片方は返す。"""
    url = check_url(url)
    folder = out_dir() / "lp"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: dict = {}

    speed, speed_path = measure_speed(url, get, lighthouse, folder, stamp, max_age_hours)
    if speed_path:
        saved["表示速度の生データ"] = str(speed_path)

    page: dict
    try:
        status, final_url, body = get(url, PAGE_TIMEOUT)
        if status != 200:
            raise LpError(f"HTTP {status}。ページが公開されていないか、URL が違う")
        html = body.decode("utf-8", "replace")
        path = folder / f"page_{_slug(url)}_{stamp}.html"
        path.write_text(html, encoding="utf-8")
        saved["ページの HTML"] = str(path)
        page = {"取れたか": True, **parse_page(html)}
        if final_url.rstrip("/") != url.rstrip("/"):
            page["転送先"] = final_url
    except LpError as e:
        page = {"取れたか": False, "理由": str(e)}

    return {"URL": url, "表示速度（スマホ）": speed, "ページの要素": page, "生データの保存先": saved or None}
