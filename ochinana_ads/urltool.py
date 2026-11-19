"""最終ページ URL・UTM・サフィックス・ValueTrack の組み立てと分解。外へは何も送らない（URL を開きに行かない）。

ValueTrack の一覧と使える場所の決まりは、呼ぶたびに知識ベース 15 の「5. URL と計測のルール」の表から読む。
"""
from __future__ import annotations

import re
import urllib.parse as up
from typing import Dict, List, Optional, Tuple

from . import kb

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
SHORTENERS = ("bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly", "is.gd", "buff.ly", "lin.ee", "amzn.to", "x.gd")
MAX_URL = 2048
SAFE = "{}_:-.~!$'()*,;=@/+"
_TOKEN = re.compile(r"\{([^{}]+)\}")


def _valuetrack_rules() -> Tuple[Dict[str, List[dict]], Optional[str]]:
    """知識ベース 15 の 5 章の表から、{…} の名前 → その名前が出てくる行。読めなければ空と理由。"""
    try:
        doc = kb.find_doc("15")
    except kb.KBError as ex:
        return {}, str(ex)
    table: Dict[str, List[dict]] = {}
    for u in doc["units"]:
        if u["type"] != "表の行" or not u["section"].startswith("5."):
            continue
        c = u["cells"]
        target, rule = kb._cell(c, "対象"), kb._cell(c, "ルール")
        for where, text in (("対象", target), ("ルール", rule)):
            for name in _TOKEN.findall(text):
                key = _key(name)
                row = {"番号": kb._cell(c, "番号"), "対象": target, "ルール": rule, "印": kb._cell(c, "印"), "行": u["line"],
                       "出てくる所": where}
                rows = table.setdefault(key, [])
                if not any(r["番号"] == row["番号"] for r in rows):
                    rows.append(row)
    for rows in table.values():
        rows.sort(key=lambda r: r["出てくる所"] != "対象")
    return table, None


def _key(name: str) -> str:
    n = name.strip().casefold()
    if ":" in n:
        return n.split(":", 1)[0] + ":"
    return n


def _pairs(query: str) -> List[Tuple[str, str]]:
    return up.parse_qsl(query, keep_blank_values=True)


def _non_ascii(text: str) -> List[str]:
    return list(dict.fromkeys(ch for ch in text if ord(ch) > 127))


def _encode(text: str) -> str:
    return up.quote(text, safe=SAFE + "?&#%")


def _utm(utm: Optional[dict]) -> List[Tuple[str, str]]:
    out = []
    for k, v in (utm or {}).items():
        key = str(k).strip()
        if not key.startswith("utm_"):
            key = "utm_" + key
        if v is None or str(v) == "":
            continue
        out.append((key, str(v)))
    return out


def _host_notes(host: str) -> List[str]:
    h = host.casefold()
    if any(h == s or h.endswith("." + s) for s in SHORTENERS):
        return [f"{host} は短縮 URL のサービスに見える。短縮 URL は gclid・gad_* を落とす原因（24 の 9 章）で、"
                "最終ページ URL には使わない（別ドメインへのリダイレクトも不可: 15 の 5-19）"]
    return []


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall(text)


def _check_tokens(texts: Dict[str, str], campaign_type: str, rules: Dict[str, List[dict]], kb_error: Optional[str]) -> Tuple[List[str], List[dict]]:
    problems, used = [], []
    ct = kb.norm(campaign_type)
    seen = set()
    for where, text in texts.items():
        for name in _tokens(text):
            key = _key(name)
            if (where, key) in seen:
                continue
            seen.add((where, key))
            if key.startswith("_"):
                pname = name[1:]
                ok = bool(re.fullmatch(r"[A-Za-z0-9]{1,16}", pname))
                used.append({"ValueTrack": "{" + name + "}", "場所": where, "種類": "カスタム パラメータ",
                             "決まり": "名前は 16 文字まで・英数字だけ（15 の 5-07）。URL では {_名前}（5-10）"})
                if not ok:
                    problems.append(f"カスタム パラメータの名前 {pname!r} は英数字 16 文字までにする（15 の 5-07）")
                continue
            rows = rules.get(key, [])
            if where == "サフィックス" and (key.startswith("lpurl") or key in ("unescapedlpurl", "escapedlpurl", "ignore")):
                problems.append(f"サフィックスに {{{name}}} は入れられない（15 の 5-13）")
            if where == "最終ページ URL" and (key.startswith("lpurl") or key in ("unescapedlpurl", "escapedlpurl")):
                problems.append(f"{{{name}}} はトラッキング テンプレート専用（15 の 5-31）。最終ページ URL には入れない")
            if not rows:
                if kb_error:
                    used.append({"ValueTrack": "{" + name + "}", "場所": where, "決まり": f"知識ベースを読めなかった（{kb_error}）"})
                else:
                    problems.append(f"{{{name}}} は知識ベース（15 の 5 章）の一覧に無い。対応していない ValueTrack は保存できない（15 の 5-48）")
                    used.append({"ValueTrack": "{" + name + "}", "場所": where, "決まり": "一覧に無い"})
                continue
            r = rows[0]
            used.append({"ValueTrack": "{" + name + "}", "場所": where, "番号": r["番号"], "決まり": r["ルール"], "印": r["印"]})
            if ct:
                text_n = kb.norm(r["ルール"])
                for bad in ("非対応", "は使えない", "では使えない"):
                    pos = text_n.find(ct)
                    if pos >= 0 and bad in text_n[pos: pos + len(ct) + 12]:
                        problems.append(f"{{{name}}} は {campaign_type} では使えないと書かれている（15 の {r['番号']}）")
                        break
    return problems, used


def _split(url: str) -> Dict[str, object]:
    p = up.urlsplit(url)
    return {"スキーム": p.scheme, "ホスト": p.hostname, "パス": p.path, "クエリ": p.query, "フラグメント（# の後ろ）": p.fragment}


def parse(url: str) -> dict:
    url = str(url or "").strip()
    if not url:
        raise ValueError("分解する URL を渡す")
    parts = _split(url)
    params = [{"名前": k, "値（デコード後）": v} for k, v in _pairs(str(parts["クエリ"]))]
    names = [p["名前"] for p in params]
    dup = sorted({n for n in names if names.count(n) > 1})
    rules, kb_error = _valuetrack_rules()
    problems, used = _check_tokens({"URL": url}, "", rules, kb_error)
    notes = []
    if dup:
        problems.append(f"同じ名前のパラメータが 2 回以上ある: {dup}")
    if "gclid" in [n.casefold() for n in names]:
        notes.append("gclid が入っている。自動タグ設定がクリックごとに付けるもの（15 の 5-29）。手で入れない")
    if parts["フラグメント（# の後ろ）"] and "?" in str(parts["フラグメント（# の後ろ）"]):
        problems.append("# の後ろに ? がある。# の後ろはサーバーに届かず、gad_* などのパラメータは # の前に置く（24 の 24-07）")
    na = _non_ascii(url)
    if na:
        notes.append(f"エンコードしていない文字がある（{''.join(na[:10])}）。エンコードした形: {_encode(url)}")
    notes += _host_notes(str(parts["ホスト"] or ""))
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "URL の部品",
        "URL": url,
        "部品": parts,
        "パラメータ": params,
        "UTM": {k: v for k, v in _pairs(str(parts["クエリ"])) if k in UTM_KEYS} or None,
        "ValueTrack": used or None,
        "問題": problems or None,
        "注記": notes or None,
    }


def build(final_url: str = "", utm: Optional[dict] = None, suffix: str = "", extra_params: Optional[dict] = None,
          campaign_type: str = "") -> dict:
    final_url = str(final_url or "").strip()
    if not final_url:
        raise ValueError("final_url（最終ページ URL）を渡す。URL を分解するだけなら parse_url を渡す")
    problems: List[str] = []
    notes: List[str] = []
    refs = []
    parts = up.urlsplit(final_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        problems.append("最終ページ URL は http:// か https:// で始め、ホスト名を入れる（15 の 5-18）")
    refs.append(("15", "5-18", ("http",)))
    suffix = str(suffix or "").strip()
    if suffix[:1] in ("?", "&"):
        problems.append("サフィックスは ? や & で始めない（15 の 5-12）。組み立てでは先頭の記号を外した")
        suffix = suffix.lstrip("?&")
    refs.append(("15", "5-12", ("?",)))
    pieces: List[Tuple[str, str, str]] = []  # (名前, 値, 出どころ)
    for k, v in _pairs(parts.query):
        pieces.append((k, v, "最終ページ URL"))
    suffix_pairs = _pairs(suffix)
    for k, v in suffix_pairs:
        pieces.append((k, v, "サフィックス（渡したもの）"))
    for k, v in _utm(utm):
        pieces.append((k, v, "UTM"))
    for k, v in (extra_params or {}).items():
        pieces.append((str(k), "" if v is None else str(v), "ほかのパラメータ"))
    names: Dict[str, List[str]] = {}
    for k, _, src in pieces:
        names.setdefault(k.casefold(), []).append(src)
    dups = {k: v for k, v in names.items() if len(v) > 1}
    for k, srcs in dups.items():
        problems.append(f"パラメータ {k} が {len(srcs)} 回ある（{'・'.join(srcs)}）。どれが使われるかは受け取る側次第なので 1 つにする")
    bad_utm = [k for k, _, src in pieces if k.startswith("utm_") and k not in UTM_KEYS]
    if bad_utm:
        notes.append(f"UTM の名前 {bad_utm} は標準の 5 つ（{'、'.join(UTM_KEYS)}。33 の用語集）に無い")
    if any(src == "UTM" for _, _, src in pieces):
        notes.append("GA4 では自動タグ設定（gclid）が手で付けた UTM より優先する。UTM は他の分析ツール用か補助（24 の 24-05）")
        refs.append(("24", "24-05", ("UTM",)))
    if any(k.casefold() == "gclid" for k, _, _ in pieces):
        problems.append("gclid を手で入れている。自動タグ設定がクリックごとに付ける（15 の 5-29）。手で入れない")
    # サフィックスは、最終ページ URL に元から付いているもの以外をまとめる
    add = [(k, v) for k, v, src in pieces if src != "最終ページ URL"]
    built_suffix = "&".join(f"{up.quote(k, safe=SAFE)}={up.quote(v, safe=SAFE)}" for k, v in add)
    base = up.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    landing = base + (("&" if parts.query else "?") + built_suffix if built_suffix else "")
    if parts.fragment:
        landing += "#" + parts.fragment
        problems.append("最終ページ URL に # がある。パラメータは # の前に置かないとページ側で読めない（gad_* は # の前: 24 の 24-07）。"
                        "見本は # の前に入れた。Google がサフィックスを # の前に入れるかは知識ベースに無い（❓）。# の無い URL にするのが安全")
        refs.append(("24", "24-07", ("#",)))
    na = _non_ascii(final_url) + _non_ascii(suffix) + _non_ascii("".join(f"{k}{v}" for k, v, _ in pieces))
    if _non_ascii(final_url):
        notes.append(f"最終ページ URL にエンコードしていない文字がある（{''.join(_non_ascii(final_url)[:10])}）。"
                     f"エンコードした形: {_encode(final_url)}。URL の長さはエンコード後で数える（25 の 25-02）")
        refs.append(("25", "25-02", ("エンコード",)))
    elif na:
        notes.append("パラメータの値の日本語は、組み立てでエンコードした")
    if len(landing) > MAX_URL:
        problems.append(f"着地 URL が {len(landing)} 文字。最終ページ URL は半角 2,048 文字まで（15 の 5-22）")
    refs.append(("15", "5-22", ("2,048",)))
    notes += _host_notes(parts.hostname or "")
    rules, kb_error = _valuetrack_rules()
    p2, used = _check_tokens({"最終ページ URL": final_url, "サフィックス": built_suffix}, campaign_type, rules, kb_error)
    problems += p2
    refs += [("15", "5-29", ("GCLID",)), ("15", "5-19", ("リダイレクト",)), ("15", "5-14", ("サフィックス",))]
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "URL の文字列",
        "入力": {"最終ページ URL": final_url, "UTM": dict(utm or {}) or None, "サフィックス": suffix or None,
                "ほかのパラメータ": dict(extra_params or {}) or None, "キャンペーンの種類": campaign_type or "指定なし"},
        "最終ページ URL（入力欄に入れる）": base + ("#" + parts.fragment if parts.fragment else ""),
        "最終ページ URL のサフィックス（入力欄に入れる）": built_suffix or None,
        "着地 URL の見本": landing,
        "ValueTrack": used or None,
        "問題": problems or None,
        "注記": notes + [
            "ページで使うパラメータ（UTM など）は、トラッキング テンプレートではなくサフィックスに入れる（15 の 5-14）。",
            "リダイレクト・短縮 URL・知らないパラメータを拒むサーバー・別ドメインへの移動は gclid・gad_* を落とす（24 の 9 章、15 の 5-29）。"
            "自分の広告から着地して、URL に gclid が残るかを確かめる。",
        ],
        "根拠": [kb.cite(f, k, expect=e) for f, k, e in refs],
        "点検の範囲": "文字列としての点検だけ。ページは開いていない（リダイレクトの有無・ページの有無は見ていない）。",
    }
