"""知見カード（ads-intel/）を読む。読むだけで、カードは書き換えない（扱いを決めるのは運用者）。

- 返すのは要点（AI が自分の言葉で書いた 1〜2 文、段、信頼度、扱い、期限、出どころの URL）。
  取り込んだ記事の抜粋（inbox/）は読まないし、返さない。
- アカウントとの照らし合わせは、機械で言える範囲だけ。分からないところは事実を並べて「判断できない」と返す。
"""
from __future__ import annotations

import json
import os
import platform
import re
import unicodedata
from datetime import date, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional

ENV_INTEL_DIR = "ADS_INTEL_DIR"
DEFAULT_INTEL_DIR = ""  # 公開版は既定の場所を持たない。ADS_INTEL_DIR で知見カードのフォルダを指す
STATUSES = ("未決", "無視", "見張る", "確かめる", "基準に反映する")
TIERS = ("A", "A'", "B", "C")
TIER_JA = {"A": "A（公式）", "A'": "A'（API の仕様の差分）", "B": "B（実務家。仮説として扱う）", "C": "C（自分の実測）"}
DEFAULT_SINCE_DAYS = 7

_FRONT = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)


class IntelError(ValueError):
    pass


def intel_dir() -> Path:
    raw = os.environ.get(ENV_INTEL_DIR) or DEFAULT_INTEL_DIR
    if not raw:
        raise IntelError(f"知見カードの場所が設定されていない。環境変数 {ENV_INTEL_DIR} で、cards/ を持つフォルダを指す")
    p = Path(raw)
    if not (p / "cards").is_dir():
        raise IntelError(f"知見カードのフォルダが見つからない: {p / 'cards'}（環境変数 {ENV_INTEL_DIR} で場所を指す）")
    return p


def _scalar(text: str) -> Any:
    v = text.strip()
    if v == "":
        return ""
    if v[0] in '"[{':
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def parse_card(path: Path) -> Dict[str, Any]:
    """カードの先頭（--- で挟んだ部分）と、本文の最初の見出し（元の題名）を読む。入れ子は 1 段まで。"""
    m = _FRONT.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}
    data: Dict[str, Any] = {}
    parent: Optional[str] = None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw.startswith("  ")
        key, sep, value = raw.strip().partition(":")
        if not sep:
            continue
        v = value.strip()
        if v and v[0] not in '"[{' and " #" in v:
            v = v.split(" #", 1)[0]
        if indented and parent:
            data[parent][key.strip()] = _scalar(v)
        elif v == "":
            parent = key.strip()
            data[parent] = {}
        else:
            parent = None
            data[key.strip()] = _scalar(v)
    for k, v in list(data.items()):
        if v == {}:
            data[k] = ""
    title = re.search(r"^# (.+)$", m.group(2), re.M)
    data["題名"] = title.group(1).strip() if title else ""
    data["_file"] = str(path)
    return data


def load_cards(folder: Optional[Path] = None) -> List[Dict[str, Any]]:
    folder = folder or intel_dir()
    cards = []
    for p in sorted((folder / "cards").glob("*.md")):
        if p.name.startswith("_"):
            continue
        c = parse_card(p)
        if c.get("id"):
            cards.append(c)
    return cards


def _source(card: dict) -> dict:
    s = card.get("出どころ")
    return s if isinstance(s, dict) else {}


def _iso(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_draft(card: dict) -> bool:
    """AI の下書きのままで、人の確認がまだか。"""
    return "確認はまだ" in str(card.get("下書き", "")) or card.get("扱い") == "未決"


def cautions(card: dict) -> List[str]:
    """カードを使う前に必ず伝えること。"""
    out = []
    if is_draft(card):
        out.append("このカードは AI の下書きで、人の確認がまだ。要点は出どころを読んで確かめてから使う。")
    if card.get("信頼度") == "仮説":
        out.append("信頼度は「仮説」。出どころは実務家の発信で、裏は取れていない。そのまま基準や自動の変更にしない。")
    if card.get("扱い") == "未決":
        out.append("扱いが未決。無視・見張る・確かめる・基準に反映する、のどれにするかは運用者が決める。")
    if card.get("注意"):
        out.append(f"カードの注意書き: {card['注意']}")
    return out


def brief(card: dict, today: Optional[date] = None) -> dict:
    """一覧に出す 1 行。"""
    today = today or date.today()
    deadline = _iso(card.get("期限"))
    row = {
        "id": card["id"],
        "題名": card.get("題名", ""),
        "段": card.get("段") or _source(card).get("段", ""),
        "信頼度": card.get("信頼度", ""),
        "知見の種類": card.get("知見の種類", ""),
        "出どころの日付": _source(card).get("日付", ""),
        "入った日": str(card.get("入った日", "")),
        "扱い": card.get("扱い", ""),
        "期限": str(card.get("期限", "")),
    }
    if deadline:
        row["期限まで（日）"] = (deadline - today).days
    return row


def detail(card: dict, today: Optional[date] = None) -> dict:
    """探したときに返す要点。"""
    s = _source(card)
    return {
        **brief(card, today),
        "何が変わったか": card.get("何が変わったか", ""),
        "関係する設定": card.get("関係する設定") or [],
        "関係する基準と点検": card.get("関係する基準と点検") or [],
        "出どころ": {"名前": s.get("名前", ""), "URL": s.get("url", "")},
        "人の確認": "まだ（AI の下書き）" if is_draft(card) else "済み",
    }


def _clamp(n, default=20, maximum=100) -> int:
    try:
        return max(1, min(int(n), maximum))
    except (TypeError, ValueError):
        return default


def get_new_intel(since: Optional[str] = None, top_n: int = 20, today: Optional[date] = None) -> dict:
    today = today or date.today()
    if since:
        start = _iso(since)
        if not start or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(since)):
            raise IntelError(f"日付は YYYY-MM-DD で指定する（受け取った値: {since!r}）")
    else:
        start = today - timedelta(days=DEFAULT_SINCE_DAYS)
    top_n = _clamp(top_n)
    folder = intel_dir()
    cards = load_cards(folder)
    new = [c for c in cards if (_iso(c.get("入った日")) or date.min) >= start]
    new.sort(key=lambda c: (str(c.get("入った日", "")), _source(c).get("日付", "")), reverse=True)
    undecided = [c for c in cards if c.get("扱い") == "未決"]
    dated = sorted((c for c in undecided if _iso(c.get("期限"))), key=lambda c: _iso(c["期限"]))
    tiers: Dict[str, int] = {}
    for c in new:
        t = _source(c).get("段", "不明")
        tiers[t] = tiers.get(t, 0) + 1
    digests = sorted(p for p in (folder / "digest").glob("*.md") if (_iso(p.stem) or date.min) >= start) \
        if (folder / "digest").is_dir() else []
    return {
        "対象アカウント": "なし（知見カード）",
        "期間": f"{start.isoformat()} 以降に入ったもの（今日は {today.isoformat()}）",
        "単位": "知見カード 1 枚ごと",
        "件数": {
            "この期間に入ったカード": len(new),
            "段ごと": tiers,
            "扱いが未決のカード（全期間）": len(undecided),
            "うち期限つき": len(dated),
            "うち期限を過ぎたもの": sum(1 for c in dated if _iso(c["期限"]) < today),
            "うち期限まで 30 日以内": sum(1 for c in dated if 0 <= (_iso(c["期限"]) - today).days <= 30),
            "カードの総数": len(cards),
        },
        "新しく入ったもの": [brief(c, today) for c in new[:top_n]],
        "未決で期限つきのもの（期限の近い順）": [brief(c, today) for c in dated[:top_n]],
        "毎日のまとめ": [str(p) for p in digests[-7:]],
        "扱い方": "カードの要点は AI の下書き。段 B は仮説。扱いを決めるのは運用者で、この道具はカードを書き換えない。",
    }


def _norm(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text)).casefold()


def _haystack(card: dict) -> str:
    s = _source(card)
    parts = [card.get("id", ""), card.get("題名", ""), card.get("何が変わったか", ""), s.get("名前", ""),
             " ".join(map(str, card.get("関係する設定") or [])),
             " ".join(map(str, card.get("関係する基準と点検") or [])), card.get("知見の種類", "")]
    return _norm(" ".join(map(str, parts)))


def search_cards(query: str = "", status: Optional[str] = None, tier: Optional[str] = None,
                 top_n: int = 20, today: Optional[date] = None) -> dict:
    if status and status not in STATUSES:
        raise IntelError(f"扱いは {' / '.join(STATUSES)} のどれか（受け取った値: {status!r}）")
    if tier and tier not in TIERS:
        raise IntelError(f"段は {' / '.join(TIERS)} のどれか（受け取った値: {tier!r}）")
    top_n = _clamp(top_n)
    words = [w for w in _norm(query or "").split() if w]
    hits = []
    for c in load_cards():
        if status and c.get("扱い") != status:
            continue
        if tier and _source(c).get("段") != tier:
            continue
        text = _haystack(c)
        if all(w in text for w in words):
            hits.append(c)
    hits.sort(key=lambda c: (_source(c).get("日付", ""), c["id"]), reverse=True)
    return {
        "対象アカウント": "なし（知見カード）",
        "期間": "カードの全期間",
        "単位": "知見カード 1 枚ごと（出どころの日付の新しい順）",
        "条件": {"語（すべて含む）": words, "扱い": status or "指定なし", "段": tier or "指定なし"},
        "件数": {"当たったカード": len(hits), "表示している件数": min(top_n, len(hits))},
        "行": [detail(c, today) for c in hits[:top_n]],
        "扱い方": "「何が変わったか」は AI が書いた下書きの要点。使う前に出どころを読む。",
    }


def find_card(card_id: str) -> dict:
    wanted = str(card_id).strip()
    cards = load_cards()
    exact = [c for c in cards if c["id"] == wanted]
    if exact:
        return exact[0]
    partial = [c for c in cards if wanted and wanted in c["id"]]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise IntelError(f"カード ID {wanted!r} に当たるカードが {len(partial)} 枚ある。ID を全部書く")
    raise IntelError(f"カード ID {wanted!r} が見つからない。search_cards で探す")


# ---- アカウントとの照らし合わせ ----

CHANNEL_TOPICS = (
    ("スマート キャンペーン", r"スマート ?キャンペーン|smart ?campaign", "SMART"),
    ("P-MAX", r"p-?max|performance ?max|アセット ?グループ|asset_?group", "PERFORMANCE_MAX"),
    ("デマンド ジェネレーション", r"デマンド ?ジェネレーション|demand[ _]?gen", "DEMAND_GEN"),
    ("検索キャンペーン", r"検索キャンペーン|検索広告|search ?campaign", "SEARCH"),
    ("ショッピング", r"ショッピング|shopping|merchant", "SHOPPING"),
    ("動画キャンペーン", r"動画キャンペーン|youtube|video", "VIDEO"),
    ("アプリ キャンペーン", r"アプリ ?キャンペーン|app ?campaign", "MULTI_CHANNEL"),
)
YES, PAUSED_ONLY, NO, UNKNOWN = "当てはまる", "当てはまる（一時停止のものだけ）", "当てはまらない", "判断できない"


def tool_facts() -> dict:
    """この道具そのものの状態（どの版の API とライブラリで動いているか）。"""
    from .client import api_version

    try:
        library = metadata.version("google-ads")
    except metadata.PackageNotFoundError:
        library = "不明"
    return {"API の版": api_version(), "google-ads ライブラリ": library, "Python": platform.python_version()}


def _channel_row(term: str, label: str, channel: str, state: dict) -> dict:
    mine = [c for c in state["campaigns"] if c["channel"] == channel]
    enabled = sum(1 for c in mine if c["status"] == "ENABLED")
    paused = len(mine) - enabled
    verdict = YES if enabled else (PAUSED_ONLY if paused else NO)
    return {"カードの語": term, "見たもの": f"キャンペーンの種類: {label}",
            "アカウントの状態": f"有効 {enabled} 本・一時停止 {paused} 本", "判定": verdict}


def match_terms(card: dict, state: dict, facts: dict) -> Dict[str, list]:
    """カードの「関係する設定」の語を 1 つずつ、アカウントと道具の状態に照らす。"""
    terms = [str(t) for t in (card.get("関係する設定") or [])]
    rows, unmatched, seen = [], [], set()

    def add(key: str, row: dict) -> None:
        if key not in seen:
            seen.add(key)
            rows.append(row)

    def scan(term: str) -> bool:
        t = _norm(term)
        hit = False
        for label, pattern, channel in CHANNEL_TOPICS:
            if re.search(pattern, t):
                add(channel, _channel_row(term, label, channel, state))
                hit = True
        if re.search(r"ai ?max|ai最大化", t):
            on = [c for c in state["campaigns"] if c.get("ai_max")]
            enabled = sum(1 for c in on if c["status"] == "ENABLED")
            add("ai_max", {"カードの語": term, "見たもの": "AI Max を有効にしているキャンペーン",
                           "アカウントの状態": f"有効 {enabled} 本・一時停止 {len(on) - enabled} 本",
                           "判定": YES if enabled else (PAUSED_ONLY if on else NO)})
            hit = True
        for v in re.findall(r"\bv(\d{2})(?:[._]\d+)?", t):
            mine = facts.get("API の版", "")
            same = mine.lstrip("v") == v
            add(f"api_v{v}", {"カードの語": term, "見たもの": f"この道具が使っている API の版（v{v} か）",
                              "アカウントの状態": f"この道具は {mine}", "判定": YES if same else NO})
            hit = True
        py = re.search(r"python ?3\.(\d+)", t)
        if py:
            mine = facts.get("Python", "")
            same = mine.startswith(f"3.{py.group(1)}.") or mine == f"3.{py.group(1)}"
            add(f"python{py.group(1)}", {"カードの語": term, "見たもの": "この道具が動いている Python の版",
                                        "アカウントの状態": f"この道具は Python {mine}", "判定": YES if same else NO})
            hit = True
        if re.search(r"campaigncriterion\.language|言語(の)?(指定|条件|ターゲ)", t):
            n = state.get("language_criteria", 0)
            add("language", {"カードの語": term, "見たもの": "キャンペーンの言語の指定",
                             "アカウントの状態": f"言語の指定が {n} 件", "判定": YES if n else NO})
            hit = True
        if re.search(r"開発者トークン|api ?アクセス|レート制限|request_id", t):
            add("api_use", {"カードの語": term, "見たもの": "Google 広告 API を使っているか",
                            "アカウントの状態": "この道具は Google 広告 API でアカウントを読んでいる", "判定": YES})
            hit = True
        return hit

    for term in terms:
        if not scan(term):
            unmatched.append(term)
    if not rows:  # 設定の語から何も分からなければ、要点の文も見る
        scan(str(card.get("何が変わったか", "")))
        for r in rows:
            r["カードの語"] = "（要点の文から）"
    return {"rows": rows, "unmatched": unmatched}


def overall(rows: List[dict]) -> Dict[str, str]:
    verdicts = [r["判定"] for r in rows]
    if not verdicts:
        return {"判定": UNKNOWN, "理由": "カードの語を、道具で読める状態（キャンペーンの種類、AI Max、API の版など）に結び付けられなかった。"}
    if YES in verdicts:
        hit = [r["見たもの"] for r in rows if r["判定"] == YES]
        return {"判定": YES, "理由": "関係する設定を使っている: " + "、".join(hit) + "。"}
    if PAUSED_ONLY in verdicts:
        return {"判定": PAUSED_ONLY, "理由": "関係するものはあるが、有効なものは無い。再開するときに関係する。"}
    return {"判定": NO, "理由": "関係する設定を、このアカウント（とこの道具）は使っていない。"}


def match_card(card: dict, state: dict, facts: dict, account_label: str, today: Optional[date] = None) -> dict:
    m = match_terms(card, state, facts)
    verdict = overall(m["rows"])
    from .checks import BIDDING_JA
    from .convert import CHANNEL_JA

    by_channel: Dict[str, Dict[str, int]] = {}
    for c in state["campaigns"]:
        row = by_channel.setdefault(CHANNEL_JA.get(c["channel"], c["channel"]), {"有効": 0, "一時停止": 0})
        row["有効" if c["status"] == "ENABLED" else "一時停止"] += 1
    return {
        "対象アカウント": account_label,
        "期間": "指定なし（いまの設定）",
        "単位": "知見カード 1 枚と、アカウントのいまの状態",
        "カード": detail(card, today),
        "必ず伝えること": cautions(card),
        "判定": verdict["判定"],
        "理由": verdict["理由"],
        "照らした点": m["rows"],
        "機械で照らせなかった語": m["unmatched"],
        "アカウントの状態（道具で読める範囲）": {
            "キャンペーンの種類ごとの本数": by_channel,
            "入札戦略（有効なキャンペーン）": sorted({BIDDING_JA.get(c["bidding"], c["bidding"])
                                      for c in state["campaigns"] if c["status"] == "ENABLED"}),
            "AI Max が有効なキャンペーン": [c["name"] for c in state["campaigns"] if c.get("ai_max") and c["status"] == "ENABLED"],
            "直近 30 日の成果の件数": state.get("conversions_30d"),
            "言語の指定の件数": state.get("language_criteria"),
            "この道具": facts,
        },
        "判定の範囲": "「当てはまる」は、関係する設定を使っているという意味。対応が要るかどうかは見ていない。"
                  "照らせなかった語と、この道具の外の仕組み（スクリプト、ほかのプログラム）は判定に入っていない。",
    }
