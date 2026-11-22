"""知識ベース（媒体仕様メモ/Google広告の基礎/）を引く。読むだけで、メモは書き換えない。

- メモは今も増えたり直されたりしているので、中身をコードに写さない。呼ばれるたびにフォルダを見て、
  更新時刻と大きさが変わったファイルだけ読み直す（索引はこの起動の中だけ持つ）。
- 返すのは「どのファイルのどの節（表なら何行目）か」「確認日」「印（✅△❓）」「根拠の種類」と、短く切った本文。
- 判定はしない。並べるだけ。答えるのは Claude で、根拠を確かめるのは運用者。
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENV_KB_DIR = "ADS_KB_DIR"
DEFAULT_KB_DIR = str(Path(__file__).resolve().parent.parent / "kb" / "Google広告の基礎")  # 同梱の知識ベース。ADS_KB_DIR で差し替えられる

KATA = "運用の型（体系）"   # 運用の型/ フォルダ（型00〜型12、付録 型90〜）
CORE = "核となる理解"       # 90_核となる理解.md（無いこともある）
RULE_TABLE = "ルール統合表"  # 92_ルール表_統合.md（rules.csv と同じ中身）
QUIZ = "理解度テスト"        # 93_理解度テスト.md（quiz.jsonl と同じ中身）
CHECKED = "裏取りの記録"     # 94_裏取りの記録_*.md
KINDS = (CORE, KATA, RULE_TABLE, "知識メモ", "運用の型", "ロジックツリー", "Tips", CHECKED, QUIZ)
# kb_search の並び（見出しの一致の次に効く）。28・35〜37 の「運用の型」メモは知識メモと同じ段。
KIND_RANK = {CORE: 0, KATA: 1, RULE_TABLE: 2, "知識メモ": 3, "運用の型": 3, "ロジックツリー": 4, "Tips": 5, CHECKED: 6, QUIZ: 7}
_ALIAS_SRC = {
    "知識メモ": ("知識メモ", "メモ", "仕様"),
    "Tips": ("tips", "tip"),
    "ロジックツリー": ("ロジックツリー", "ツリー"),
    "運用の型": ("運用の型", "運用の型（メモ）"),
    KATA: (KATA, "体系", "型", "運用の型体系", "型の体系"),
    RULE_TABLE: (RULE_TABLE, "統合表", "ルール表"),
    QUIZ: (QUIZ, "テスト", "quiz"),
    CHECKED: (CHECKED, "裏取り"),
    CORE: (CORE, "核"),
}
KIND_ALIASES = {unicodedata.normalize("NFKC", a).casefold().replace(" ", ""): k for k, al in _ALIAS_SRC.items() for a in al}
MARKS = ("✅", "△", "❓")
EVIDENCE_WORDS = ("公式", "データあり", "経験則", "推測", "第三者", "研究", "論文", "裁判", "実務家", "このメモ", "API", "詳細")
TIPS_CAUTION = "Tips は実務家などの主張。仮説として扱い、そのまま基準や変更の根拠にしない（原則 3）。"

DEFAULT_RESULTS = 10
MAX_RESULTS = 50
DEFAULT_CHARS = 6000
MAX_CHARS = 30000
CELL_CHARS = 400

_cache: Dict[str, Tuple[int, int, dict]] = {}


class KBError(ValueError):
    pass


class KBAmbiguous(KBError):
    """番号だけでは 1 本に決まらない。候補を持たせて、呼ぶ側に選ばせる（黙って 1 本目を返さない）。"""

    def __init__(self, file: str, docs: List[dict]):
        self.candidates = [{"指定": d.get("ref", d["id"]), "ファイル": d["rel"], "題名": d["title"], "種類": d["kind"]} for d in docs]
        names = "、".join(f"{c['指定']}（{c['ファイル']}）" for c in self.candidates[:8])
        super().__init__(f"ファイル {file!r} に当たるメモが {len(docs)} 本ある。1 本に決まらないので、次のどれかで指定する: {names}")


# ---------------- 場所と読み込み ----------------

# 媒体ごとの知識ベース。Google のフォルダ（ADS_KB_DIR か既定）と同じ階層にある「◯◯広告の基礎」を引く。
# 媒体ごとに場所を変えたいときは ADS_KB_DIR_META などで指す。章（*.md）が増えても、呼ぶたびにフォルダを読み直すので拾える。
MEDIA = {"google": "Google広告の基礎", "meta": "Meta広告の基礎", "lineyahoo": "LINEヤフー広告の基礎", "chatgpt": "ChatGPT広告の基礎"}
MEDIA_LABEL = {"google": "Google 広告", "meta": "Meta 広告", "lineyahoo": "LINEヤフー広告", "chatgpt": "ChatGPT の広告"}
_MEDIA_ALIAS_SRC = {
    "google": ("google", "google広告", "グーグル", "gads"),
    "meta": ("meta", "meta広告", "facebook", "instagram", "フェイスブック", "インスタグラム", "メタ"),
    "lineyahoo": ("lineyahoo", "lineヤフー", "lineヤフー広告", "line", "yahoo", "ヤフー", "yahoo広告", "line広告", "ly"),
    "chatgpt": ("chatgpt", "chatgpt広告", "openai", "chatgptの広告"),
}
MEDIA_ALIASES = {unicodedata.normalize("NFKC", a).casefold().replace(" ", "").replace("!", ""): k
                 for k, al in _MEDIA_ALIAS_SRC.items() for a in al}
GOOGLE_ONLY = "この道具は Google 広告の知識ベースだけを引く（運用の型/・ロジックツリー/・tips/・用語集は Google にしかない）"
_MEDIA: "contextvars.ContextVar[str]" = contextvars.ContextVar("kb_media", default="google")


def media_key(media: str = "") -> str:
    """媒体の指定（google／meta／lineyahoo／chatgpt。日本語や旧称も可）を、決まった名前にする。空は google。"""
    raw = unicodedata.normalize("NFKC", str(media or "")).casefold().replace(" ", "").replace("!", "")
    if not raw:
        return "google"
    if raw in MEDIA_ALIASES:
        return MEDIA_ALIASES[raw]
    raise KBError(f"媒体 {media!r} は知識ベースに無い。google／meta／lineyahoo／chatgpt のどれかで指定する")


def current_media() -> str:
    return _MEDIA.get()


@contextmanager
def use_media(media: str = ""):
    """この中だけ、知識ベースの道具が指定した媒体のフォルダを引く（呼び出しごとに戻す）。"""
    token = _MEDIA.set(media_key(media))
    try:
        yield _MEDIA.get()
    finally:
        _MEDIA.reset(token)


def media_dir(key: str) -> Optional[Path]:
    raw = os.environ.get(ENV_KB_DIR) or DEFAULT_KB_DIR
    if key == "google":
        return Path(raw) if raw else None
    own = os.environ.get(f"{ENV_KB_DIR}_{key.upper()}")
    if own:
        return Path(own)
    return Path(raw).parent / MEDIA[key] if raw else None


def media_list() -> List[dict]:
    """引ける媒体の一覧（フォルダがあるか、メモの数）。"""
    out = []
    for key in MEDIA:
        p = media_dir(key)
        ok = bool(p and p.is_dir())
        out.append({"指定": key, "媒体": MEDIA_LABEL[key], "ある": ok,
                    "メモの数": sum(1 for f in p.rglob("*.md") if not any(x.startswith((".", "_")) for x in f.relative_to(p).parts)) if ok else 0})
    return out


def kb_dir() -> Path:
    key = _MEDIA.get()
    p = media_dir(key)
    if p is None:
        raise KBError(f"知識ベースの場所が設定されていない。環境変数 {ENV_KB_DIR} で、知識ベース（Markdown のメモ）のフォルダを指す")
    if not p.is_dir():
        where = ENV_KB_DIR if key == "google" else f"{ENV_KB_DIR}_{key.upper()}（無ければ {ENV_KB_DIR} と同じ階層の {MEDIA[key]}）"
        raise KBError(f"{MEDIA_LABEL[key]}の知識ベースのフォルダが見つからない: {p}（環境変数 {where} で場所を指す）")
    return p


def _kind(rel: Path, upper: bool) -> str:
    if upper:
        return "知識メモ"
    first = rel.parts[0] if len(rel.parts) > 1 else ""
    if first == "tips":
        return "Tips"
    if first == "ロジックツリー":
        return "ロジックツリー"
    if first == "運用の型":
        return KATA
    name = rel.name
    if "核となる理解" in name:
        return CORE
    if "ルール表" in name and "統合" in name:
        return RULE_TABLE
    if "理解度テスト" in name:
        return QUIZ
    if "裏取りの記録" in name:
        return CHECKED
    if "運用の型" in name:
        return "運用の型"
    return "知識メモ"


def _file_id(name: str) -> str:
    m = re.match(r"^((?:\d+)|(?:[A-Za-z]\d+))_", name)
    return m.group(1) if m else Path(name).stem


def _doc_id(name: str, kind: str) -> str:
    """直下のメモは番号（12）、運用の型/ のファイルは 型NN（型12。体系の中の参照の書き方と同じ）。"""
    raw = _file_id(name)
    return f"型{raw}" if kind == KATA and raw.isdigit() else raw


def list_files(folder: Optional[Path] = None) -> List[dict]:
    """知識ベースのファイル（*.md）。下のフォルダ（tips/・ロジックツリー/・運用の型/ ほか）と、1 つ上の階層の *.md。"""
    folder = folder or kb_dir()
    out = []
    for p in sorted(folder.rglob("*.md")):
        rel = p.relative_to(folder)
        if any(part.startswith((".", "_")) for part in rel.parts):
            continue
        out.append({"path": p, "rel": str(rel), "kind": _kind(rel, False), "upper": False})
    for p in (sorted(folder.parent.glob("*.md")) if _MEDIA.get() == "google" else []):  # 1 つ上の *.md は Google の横断メモ
        if not p.name.startswith((".", "_")):
            out.append({"path": p, "rel": f"../{p.name}", "kind": "知識メモ", "upper": True})
    return out


def norm(text: Any) -> str:
    """探すための形: 全角半角・大文字小文字をそろえ、空白と強調の記号を外す。"""
    t = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"[\s*`]+", "", t)


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


_SEP = re.compile(r"^\|?\s*:?-{3,}")
_DATE = re.compile(r"(確認日|書いた日|作成日|更新日|作成|更新)\s*[:：]\s*(\d{4}-\d{2}-\d{2})")


def _marks(text: str) -> List[str]:
    return [m for m in MARKS if m in text]


def _evidence(text: str) -> List[str]:
    found = []
    for w in EVIDENCE_WORDS:
        if re.search(rf"`{w}`|【{w}|〔{w}|（{w}）|^{w}$|(?:^|[\s／/、（(]){w}(?=[\s:：（(\d§]|$)", text):
            found.append(w)
    return found


def parse(path: Path, rel: str, kind: str, upper: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title, date, summary = "", None, ""
    head_bullets: Dict[str, str] = {}
    for ln in lines[:25]:
        if not title and ln.startswith("# "):
            title = ln[2:].strip()
        m = _DATE.search(ln)
        if m and not date:
            date = m.group(2)
        b = re.match(r"^-\s*([^:：]{1,10})[:：]\s*(.+)$", ln)
        if b:
            head_bullets.setdefault(b.group(1).strip(), b.group(2).strip())
    for key in ("位置づけ", "使いどころ", "対象", "対象の指標", "役割の分け方", "使い方", "読み手", "作り方"):
        if key in head_bullets:
            summary = head_bullets[key]
            break
    sections: List[dict] = []
    units: List[dict] = []
    stack: List[Tuple[int, str]] = []
    header: Optional[List[str]] = None
    table_id = -1
    i = 0
    while i < len(lines):
        ln = lines[i]
        h = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln)
        if h:
            level, t = len(h.group(1)), h.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, t))
            if sections:
                sections[-1].setdefault("end", i)
            sections.append({"level": level, "title": t, "line": i + 1,
                             "path": " > ".join(x[1] for x in stack if x[0] > 1) or t})
            units.append({"type": "見出し", "line": i + 1, "section": sections[-1]["path"], "text": t,
                          "level": level})
            header = None
            i += 1
            continue
        sec = sections[-1]["path"] if sections else ""
        if ln.lstrip().startswith("|"):
            if header is None and i + 1 < len(lines) and _SEP.match(lines[i + 1].strip()):
                header = []
                for h in _split_row(ln):  # 同じ名前の列（92 の 2-11 の「裁定」が 2 つ など）は 2 つ目以降に番号を付ける
                    name, n = h, 2
                    while name in header:
                        name, n = f"{h}（{n}）", n + 1
                    header.append(name)
                table_id += 1
                i += 2
                continue
            if header is not None:
                cells = _split_row(ln)
                row = {header[k] if k < len(header) else f"列{k + 1}": v for k, v in enumerate(cells)}
                units.append({"type": "表の行", "line": i + 1, "section": sec, "text": " | ".join(cells),
                              "cells": row, "header": header, "table": table_id})
                i += 1
                continue
        else:
            header = None
        if ln.strip() and not re.match(r"^-{3,}$", ln.strip()):
            units.append({"type": "本文", "line": i + 1, "section": sec, "text": ln.strip()})
        i += 1
    for s in sections:
        s.setdefault("end", len(lines))
    # 各節の字数（下の節を含む）
    for k, s in enumerate(sections):
        end = len(lines)
        for t in sections[k + 1:]:
            if t["level"] <= s["level"]:
                end = t["line"] - 1
                break
        s["end"] = end
        s["chars"] = len("\n".join(lines[s["line"]:end]))
    for u in units:
        u["n"] = norm(u["text"])
        u["nsec"] = norm(u["section"])
    return {
        "path": str(path), "rel": rel, "name": path.name, "id": _doc_id(path.name, kind), "raw_id": _file_id(path.name),
        "kind": kind, "upper": upper,
        "title": title or path.stem, "date": date, "summary": summary, "chars": len(text),
        "lines": lines, "sections": sections, "units": units, "mtime": path.stat().st_mtime,
    }


def load(entry: dict) -> dict:
    p: Path = entry["path"]
    st = p.stat()
    key = str(p)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    doc = parse(p, entry["rel"], entry["kind"], entry["upper"])
    _cache[key] = (st.st_mtime_ns, st.st_size, doc)
    return doc


def load_all(folder: Optional[Path] = None) -> List[dict]:
    docs = [load(e) for e in list_files(folder)]
    count: Dict[str, int] = {}
    for d in docs:
        k = _id_key(d["id"]) or norm(d["id"])
        count[k] = count.get(k, 0) + 1
    for d in docs:  # 1 本に決まる呼び名（番号が重なるファイルは名前）
        k = _id_key(d["id"]) or norm(d["id"])
        d["ref"] = d["id"] if count[k] == 1 else Path(d["name"]).stem
    return docs


def _kind_filter(kind: str) -> Optional[str]:
    if not kind:
        return None
    k = KIND_ALIASES.get(norm(kind)) or KIND_ALIASES.get(kind.strip())
    if not k:
        raise KBError(f"種類は {' / '.join(KINDS)} のどれか（受け取った値: {kind!r}）")
    return k


def _id_key(s: str) -> Optional[str]:
    """番号の形（12、05、型04、型90、T1、L2）なら、比べるための形（0 を外す）。番号の形でなければ None。"""
    n = norm(s).removesuffix(".md")
    m = re.match(r"^(型)?0*(\d+)$", n)
    if m:
        return (m.group(1) or "") + str(int(m.group(2)))
    m = re.match(r"^([a-z])0*(\d+)$", n)
    if m:
        return m.group(1) + str(int(m.group(2)))
    return None


def resolve(file: str, docs: List[dict]) -> List[dict]:
    """file に当たるファイルを全部返す（1 本に決めるのは find_doc）。

    - 番号（12、05、T1、L2）は直下・tips・ロジックツリーのメモの番号だけに当たる
    - 型NN（型04、型08、付録は 型90）は 運用の型/ のファイルだけに当たる
    - 「運用の型/12」「運用の型/12_例題.md」のようにフォルダを付けると、そのフォルダの中だけを見る
    - それ以外は名前・相対パスの一部
    """
    q = norm(file).removesuffix(".md")
    if not q:
        return list(docs)
    if "/" in q:
        folder, last = q.rsplit("/", 1)
        pool = [d for d in docs if folder in norm(str(Path(d["rel"]).parent))]
        if not last:
            return pool
        key = _id_key(last)
        if key is not None:
            return [d for d in pool if _id_key(d["raw_id"]) == key.removeprefix("型")]
        return [d for d in pool if last in norm(d["name"])]
    key = _id_key(q)
    if key is not None:
        return [d for d in docs if _id_key(d["id"]) == key]
    if q.startswith("型"):  # 「型08_判断の規則集」「型判断の規則集」
        rest = q[1:].lstrip("_")
        hits = [d for d in docs if d["kind"] == KATA and rest and rest in norm(d["name"])]
        if hits:
            return hits
    hits = [d for d in docs if q in norm(d["name"]) or q in norm(d["rel"])]
    exact = [d for d in hits if norm(Path(d["name"]).stem) == q]
    return exact if len(exact) == 1 else hits


def select(docs: List[dict], kind: str = "", file: str = "") -> List[dict]:
    k = _kind_filter(kind)
    pool = [d for d in docs if not k or d["kind"] == k]
    out = resolve(file, pool) if file else pool
    if file and not out:
        raise KBError(f"ファイル {file!r} に当たるメモが無い。kb_overview で番号と名前を確かめる"
                      "（直下のメモは番号〔12〕、運用の型/ は 型NN〔型12〕）")
    return out


def find_doc(file: str, docs: Optional[List[dict]] = None) -> dict:
    docs = docs if docs is not None else load_all()
    hits = resolve(file, docs)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise KBError(f"ファイル {file!r} が見つからない。kb_overview で番号と名前を確かめる"
                      "（直下のメモは番号〔12〕、運用の型/ は 型NN〔型12〕）")
    raise KBAmbiguous(file, hits)


# ---------------- 返答の部品 ----------------

def _clamp(n, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(n), maximum))
    except (TypeError, ValueError):
        return default


def _cut(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _where(doc: dict, unit: Optional[dict] = None) -> dict:
    w = {"ファイル": doc["rel"], "メモ番号": doc.get("ref", doc["id"]), "種類": doc["kind"], "確認日": doc["date"]}
    if doc["upper"]:
        w["置き場所"] = "1 つ上の階層（媒体仕様メモ/）"
    if unit is not None:
        w["節"] = unit["section"]
        w["行"] = unit["line"]
    return w


def _mark_of(unit: dict) -> List[str]:
    cells = unit.get("cells") or {}
    for key in ("印", "判定", "確認", "確度"):
        if key in cells and _marks(cells[key]):
            return _marks(cells[key])
    return _marks(unit["text"])


def _evidence_of(unit: dict) -> List[str]:
    cells = unit.get("cells") or {}
    for key in ("根拠の種類", "根拠", "出典"):
        if key in cells:
            found = _evidence(cells[key]) or ([cells[key].strip()] if key == "根拠の種類" and cells[key].strip() else [])
            if found:
                return found
    return _evidence(unit["text"])


def _row_view(unit: dict, cell_chars: int) -> dict:
    return {k: _cut(v, cell_chars) for k, v in (unit.get("cells") or {}).items() if k}


def _budget(items: List[dict], max_chars: int) -> Tuple[List[dict], bool]:
    out, used = [], 0
    for it in items:
        size = len(json.dumps(it, ensure_ascii=False))
        if out and used + size > max_chars:
            return out, True
        out.append(it)
        used += size
    return out, False


def _words(query: str) -> List[str]:
    return [norm(w) for w in unicodedata.normalize("NFKC", str(query or "")).split() if norm(w)]


# ---------------- 1. 一覧 ----------------

STALE_DAYS = 30


def _stale(date: Optional[str], today: Optional[str] = None) -> Optional[int]:
    """確認日から何日たったか（30 日を過ぎていれば日数、そうでなければ None）。"""
    import datetime as _dt

    if not date:
        return None
    try:
        d = _dt.date.fromisoformat(date)
        t = _dt.date.fromisoformat(today) if today else _dt.date.today()
    except ValueError:
        return None
    days = (t - d).days
    return days if days > STALE_DAYS else None


def first_reads(docs: List[dict]) -> Tuple[List[dict], List[str]]:
    """最初に読むもの: 90（核となる理解。あれば）と 運用の型/00（全体の地図）。"""
    out, notes = [], []
    core = [d for d in docs if d["kind"] == CORE]
    kata_map = [d for d in docs if d["kind"] == KATA and _id_key(d["raw_id"]) == "0"]
    for d, why in [(core[0] if core else None, "知識ベース全体を、最初に 1 回読む形にまとめたもの"),
                   (kata_map[0] if kata_map else None, "運用の型（体系）の地図。どの問いでどの章を開くか")]:
        if d is None:
            continue
        out.append({"ファイル": d["rel"], "題名": d["title"], "なぜ": why, "字数": d["chars"], "確認日": d["date"],
                    "読むには": f"kb_read(file='{d['ref']}')"})
    if _MEDIA.get() != "google":
        whole = [d for d in docs if d["kind"] == "知識メモ" and _id_key(d["raw_id"]) == "0"]
        if whole:
            d = whole[0]
            out.append({"ファイル": d["rel"], "題名": d["title"], "なぜ": "この媒体の全体像（Google との違いの表つき）",
                        "字数": d["chars"], "確認日": d["date"], "読むには": f"kb_read(file='{d['ref']}', media='{_MEDIA.get()}')"})
        if out and core:
            out[0]["読むには"] = f"kb_read(file='{core[0]['ref']}', media='{_MEDIA.get()}')"
        if not core:
            notes.append("90_核となる理解.md はまだ無い（書かれたら、ここに出る）。代わりに 00 から読む。")
        notes.append("運用の型は 28_運用の型 と 36_運用の型_◯◯への当てはめ を kb_read で読む（kb_kata は Google だけ）。")
        return out, notes
    if not core:
        notes.append("90_核となる理解.md はまだ無い（書かれたら、ここに出る）。代わりに 運用の型/00 と 00 から読む。")
    if not kata_map:
        notes.append("運用の型/00_全体の地図.md が見つからない。")
    return out, notes


def overview(kind: str = "", max_chars: int = 30000) -> dict:
    folder = kb_dir()
    every = load_all(folder)
    docs = select(every, kind)
    rows = []
    for d in docs:
        rows.append({
            "番号": d["ref"], "ファイル": d["rel"], "種類": d["kind"], "題名": d["title"],
            "一言": _cut(d["summary"], 110) if d["summary"] else None, "字数": d["chars"], "確認日": d["date"],
            "節の数": sum(1 for s in d["sections"] if s["level"] >= 2),
            "表の行": sum(1 for u in d["units"] if u["type"] == "表の行"),
        })
    by_kind: Dict[str, int] = {}
    for d in docs:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
    dates = sorted({d["date"] for d in docs if d["date"]})
    firsts, notes = first_reads(every)
    shown, cut = _budget(rows, _clamp(max_chars, 30000, 60000))
    try:
        csv_rows = rule_csv_rows(folder)
    except KBError:
        csv_rows = None
    from . import quiz as _quiz  # 循環を避けて、ここで読む

    q = _quiz.quiz_rows(folder)
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "いまのファイル（呼ぶたびに読み直す）",
        "単位": "ファイル 1 本ごと",
        "最初に読むもの": firsts,
        "媒体": MEDIA_LABEL[_MEDIA.get()],
        "場所": str(folder),
        "引ける媒体": media_list(),
        "件数": {"ファイル": len(docs), "種類ごと": by_kind, "確認日の範囲": [dates[0], dates[-1]] if dates else None,
                "確認日が書かれていないファイル": [d["rel"] for d in docs if not d["date"]],
                f"確認日から {STALE_DAYS} 日を過ぎたファイル": [d["rel"] for d in docs if _stale(d["date"])],
                "機械用の表": {RULES_CSV: len(csv_rows) if csv_rows is not None else "無い",
                            _quiz.QUIZ_FILE: len(q["rows"]) if q else "無い"}},
        "行": shown,
        "使い方": "語で探す kb_search／節を読む kb_read（直下のメモは番号 12、運用の型/ は 型12）／できる・できない kb_rules／"
                "指標が動いた kb_logic_tree／次に何をするか kb_kata（状態の判定 → 規則・しきい値・式）／"
                "理解度テストと よくある誤答 kb_quiz／用語 kb_glossary／実務家の Tips kb_tips。"
                "ほかの媒体は media（meta／lineyahoo／chatgpt）を付ける（kb_overview・kb_search・kb_read・kb_rules・kb_check_claim・kb_quiz）。",
        "注記": ["種類「運用の型（体系）」は 運用の型/ フォルダ（型00〜型12、付録 型90〜）。"
                "種類「運用の型」は名前に「運用の型」を含む直下のメモ（28・35〜37）。",
                "番号が重なるファイル（94_裏取りの記録_* など）は、番号では 1 本に決まらない。「番号」の列の名前で指定する。",
                f"確認日から {STALE_DAYS} 日を過ぎたメモは、各メモの冒頭のとおり出典を取り直してから使う。"] + notes,
    }
    if cut:
        out["注記"].append(f"文字数の上限で {len(shown)} 件で切った（全 {len(rows)} 件）。kind で絞るか max_chars を上げる。")
    return out


# ---------------- 2. 探す ----------------

def _score(unit: dict, words: List[str]) -> int:
    if not all(w in unit["n"] or w in unit["nsec"] for w in words):
        return 0
    if not any(w in unit["n"] for w in words):
        return 0  # 節の名前にしか無い行は、見出しの当たりに任せる
    s = 0
    if unit["type"] == "見出し":
        s = 100
    elif unit["type"] == "表の行":
        first = norm(" ".join(list(unit["cells"].values())[:2]))
        s = 50 if any(w in first for w in words) else 30
    else:
        s = 20
    s += sum(5 for w in words if w in unit["nsec"])
    s += min(10, sum(unit["n"].count(w) for w in words))
    return s


def _snippet(text: str, words: List[str], n: int) -> str:
    plain = re.sub(r"[*`]", "", text)
    if len(plain) <= n:
        return plain
    low = unicodedata.normalize("NFKC", plain).casefold()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, pos - n // 3)
    piece = plain[start: start + n]
    return ("…" if start else "") + piece + ("…" if start + n < len(plain) else "")


def search(query: str, kind: str = "", file: str = "", max_results: int = DEFAULT_RESULTS,
           max_chars: int = DEFAULT_CHARS, snippet_chars: int = 300) -> dict:
    words = _words(query)
    if not words:
        raise KBError("探す語を 1 つ以上渡す（空白で区切ると、すべてを含むものを探す）")
    max_results = _clamp(max_results, DEFAULT_RESULTS, MAX_RESULTS)
    max_chars = _clamp(max_chars, DEFAULT_CHARS, MAX_CHARS)
    docs = select(load_all(), kind, file)
    hits = []
    for order, d in enumerate(docs):
        rank = KIND_RANK.get(d["kind"], 9)
        for u in d["units"]:
            sc = _score(u, words)
            if sc:
                hits.append(((0 if u["type"] == "見出し" else 1, rank, -sc, order, u["line"]), d, u))
    hits.sort(key=lambda x: x[0])
    by_file: Dict[str, int] = {}
    for _, d, _ in hits:
        by_file[d["rel"]] = by_file.get(d["rel"], 0) + 1
    items = []
    for _, d, u in hits[:max_results]:
        it = {**_where(d, u), "形": u["type"]}
        if u["type"] == "表の行":
            it["行の中身"] = _row_view(u, max(80, snippet_chars // 2))
        elif u["type"] == "見出し":
            it["見出し"] = u["text"]
            sec = next((s for s in d["sections"] if s["line"] == u["line"]), None)
            if sec:
                it["節の字数"] = sec["chars"]
            it["読むには"] = f"kb_read(file='{d.get('ref', d['id'])}', section='{_cut(u['text'], 40)}')"
        else:
            it["抜粋"] = _snippet(u["text"], words, snippet_chars)
        it["印"] = _mark_of(u)
        ev = _evidence_of(u)
        if ev:
            it["根拠の種類"] = ev
        items.append(it)
    shown, cut = _budget(items, max_chars)
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        "単位": "見出し 1 つ、表の 1 行、本文の 1 段落ごと（見出しの一致を先に。その次は 核となる理解 → 運用の型（体系） → "
                "ルール統合表 → 知識メモ → ロジックツリー → Tips → 裏取りの記録 → 理解度テスト の順）",
        "条件": {"語（すべて含む）": words, "種類": kind or "指定なし", "ファイル": file or "指定なし"},
        "件数": {"当たり": len(hits), "表示": len(shown), "ファイルごとの当たり（上位）": dict(sorted(by_file.items(), key=lambda x: -x[1])[:10])},
        "行": shown,
    }
    notes = []
    if file and len(docs) > 1:
        names = "、".join(f"{d['ref']}（{d['rel']}）" for d in docs[:8])
        notes.append(f"file {file!r} は {len(docs)} 本に当たったので、全部から探した: {names}"
                     f"{' ほか' if len(docs) > 8 else ''}。1 本に絞るなら、その名前で指定する。")
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。max_chars を上げるか、file・kind で絞る。")
    if not hits:
        notes.append("当たりが無い。語を短くする、言い換える（kb_glossary で別名を引く）、空白で区切って語を減らす。"
                     "載っていないことは「知識ベースでは未確認」と扱う。")
    if notes:
        out["注記"] = notes
    return out


# ---------------- 3. 読む ----------------

def _find_section(doc: dict, section: str) -> dict:
    q = norm(section)
    secs = doc["sections"]
    num = re.match(r"^§?(\d+(?:[-.]\d+)*)\.?$", q)
    if num:
        n = num.group(1).replace(".", "-")
        for s in secs:
            t = norm(s["title"])
            m = re.match(r"^(\d+(?:[-.]\d+)*)\.?", t)
            if m and m.group(1).replace(".", "-") == n:
                return s
    exact = [s for s in secs if norm(s["title"]) == q]
    if exact:
        return exact[0]
    part = [s for s in secs if q in norm(s["title"])]
    if part:
        return part[0]
    raise KBError(f"節 {section!r} が {doc['name']} に見つからない。toc_only=true で目次を見る")


def read(file: str, section: str = "", toc_only: bool = False, max_chars: int = 8000, offset: int = 0) -> dict:
    try:
        doc = find_doc(file)
    except KBAmbiguous as ex:
        return {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
                "単位": "候補のファイル（1 本に決まらなかったので本文は返していない）",
                "条件": {"ファイル": file}, "件数": {"候補": len(ex.candidates)}, "候補": ex.candidates,
                "注記": ["番号だけでは 1 本に決まらない。候補の「指定」の値を file に渡して読み直す。"
                        "直下のメモは番号（12）、運用の型/ のファイルは 型NN（型12）で引ける。"]}
    max_chars = _clamp(max_chars, 8000, MAX_CHARS)
    base = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        **{k: v for k, v in _where(doc).items()},
        "題名": doc["title"],
    }
    if toc_only:
        toc = [{"深さ": s["level"], "見出し": s["title"], "行": s["line"], "字数": s["chars"]}
               for s in doc["sections"] if s["level"] >= 2]
        shown, cut = _budget(toc, max_chars)
        out = {**base, "単位": "目次（見出し 1 つごと。字数は下の節を含む）", "目次": shown}
        if cut:
            out["注記"] = [f"目次が長いので {len(shown)} 件で切った（全 {len(toc)} 件）。max_chars を上げる。"]
        return out
    if section:
        s = _find_section(doc, section)
        start, end, where = s["line"] - 1, s["end"], s["path"]
    else:
        start, end, where = 0, len(doc["lines"]), "全文"
    body = "\n".join(doc["lines"][start:end])
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    piece = body[offset: offset + max_chars]
    rest = max(0, len(body) - offset - len(piece))
    out = {**base, "単位": "本文（Markdown のまま）", "節": where, "行": start + 1, "本文": piece,
           "字数": {"この節": len(body), "返した": len(piece), "残り": rest},
           "印の数": {m: piece.count(m) for m in MARKS}}
    if rest:
        out["続き"] = f"offset={offset + len(piece)} で続きを読む"
    return out


# ---------------- 4. ルール ----------------

def _is_rule_table(header: List[str]) -> bool:
    h = [norm(x) for x in header]
    return any(x.startswith("ルール") for x in h) and ("対象" in h or "点" in h)


def _is_conflict_table(header: List[str]) -> bool:
    joined = norm("".join(header))
    return ("一方の記述" in joined or "片方の記述" in joined) and ("もう一方" in joined or "もう片方" in joined)


def rule_rows(docs: Iterable[dict]) -> List[Tuple[dict, dict]]:
    out = []
    for d in docs:
        for u in d["units"]:
            if u["type"] == "表の行" and _is_rule_table(u["header"]):
                out.append((d, u))
    return out


def _cell(cells: dict, *names: str) -> str:
    for k, v in cells.items():
        if norm(k) in [norm(n) for n in names] or any(norm(k).startswith(norm(n)) for n in names):
            return v
    return ""


def _rule_view(d: dict, u: dict, cell_chars: int, conflicts: Dict[Tuple[str, str], dict]) -> dict:
    c = u["cells"]
    no = _cell(c, "番号", "#")
    mark = _cell(c, "印")
    row = {
        "番号": no or None, "対象": _cut(_cell(c, "対象", "点"), cell_chars), "ルール": _cut(_cell(c, "ルール"), cell_chars),
        "印": mark or "（印の列なし）", "出典": _cut(_cell(c, "出典"), cell_chars), **_where(d, u),
    }
    if "△" in mark and (d["rel"], no) in conflicts:
        row["食い違いの記述"] = conflicts[(d["rel"], no)]
    return row


def _conflicts(docs: Iterable[dict], cell_chars: int) -> Dict[Tuple[str, str], dict]:
    out = {}
    for d in docs:
        for u in d["units"]:
            if u["type"] == "表の行" and _is_conflict_table(u["header"]):
                first = list(u["cells"].values())[0] if u["cells"] else ""
                if first and first not in ("—", "-"):
                    out[(d["rel"], first)] = {**_row_view(u, cell_chars), "節": u["section"], "行": u["line"]}
    return out


RULES_CSV = "rules.csv"
RULE_COLUMNS = ("id", "分野", "対象", "ルール", "値", "できる／できない", "条件・例外", "印", "根拠の種類", "出典 URL",
                "確認日", "元の場所", "食い違いの内容", "変更予定")
_csv_cache: Dict[str, Tuple[int, int, List[dict]]] = {}


def rule_csv_rows(folder: Optional[Path] = None) -> Optional[List[dict]]:
    """統合表 rules.csv（92 のメモと同じ中身）を読む。無ければ None。更新時刻と大きさが変わったら読み直す。"""
    import csv

    p = (folder or kb_dir()) / RULES_CSV
    if not p.is_file():
        return None
    st = p.stat()
    hit = _csv_cache.get(str(p))
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in RULE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise KBError(f"{RULES_CSV} の列が足りない: {', '.join(missing)}")
        rows = []
        for i, r in enumerate(reader, start=2):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            r["_行"] = i
            r["_n"] = norm(" ".join(r.get(c, "") for c in RULE_COLUMNS))
            rows.append(r)
    _csv_cache[str(p)] = (st.st_mtime_ns, st.st_size, rows)
    return rows


def _csv_rule_view(r: dict, cell_chars: int) -> dict:
    out = {c: _cut(r[c], cell_chars) for c in RULE_COLUMNS if c not in ("食い違いの内容", "変更予定") and r.get(c)}
    out["印"] = r["印"]
    if r["印"] == "△" or r.get("食い違いの内容"):
        out["食い違いの内容"] = r.get("食い違いの内容") or "（記載なし。元のメモの表を kb_read で読む）"  # 切らずに必ず返す
    if r.get("変更予定"):
        out["変更予定"] = r["変更予定"]  # 切らずに必ず返す
    out["ファイル"] = RULES_CSV
    out["行"] = r["_行"]
    return out


def _rules_from_csv(rows: List[dict], words: List[str], target: str, field: str, mark: str,
                    max_results: int, max_chars: int) -> dict:
    t, fl = norm(target), norm(field)
    hits = []
    for order, r in enumerate(rows):
        if fl and fl not in norm(r["分野"]):
            continue
        if t and t not in norm(r["対象"]) and t not in norm(r["ルール"]):
            continue
        if mark and r["印"] != mark:
            continue
        if words and not all(w in r["_n"] for w in words):
            continue
        head = norm(r["id"] + r["元の場所"])
        exact = sum(1 for w in words if w in head)
        key_hit = sum(1 for w in words if w in norm(r["ルール"] + r["対象"] + r["値"]))
        hits.append((-exact, -key_hit, order, r))
    hits.sort(key=lambda x: x[:3])
    items = [_csv_rule_view(r, CELL_CHARS) for *_, r in hits[:max_results]]
    shown, cut = _budget(items, max_chars)
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        "単位": "統合したルール表（rules.csv ＝ 92_ルール表_統合.md）の 1 行（1 行 1 ルール）。14・15 と各メモの「細かいルール一覧」をまとめ直したもの",
        "条件": {"語（すべて含む）": words, "対象": target or "指定なし", "分野": field or "指定なし", "印": mark or "指定なし"},
        "件数": {"当たり": len(hits), "表示": len(shown)},
        "行": shown,
        "印の読み方": "✅ 公式で確認／△ 公式の中で食い違う（「食い違いの内容」の両方を添えて答える）／❓ 公式に記載なし（事実として言い切らない）。"
                  "「変更予定」がある行は、日付の前後どちらの話かを確かめて答える。元のメモの番号（例 14#66、15:4-41）は「元の場所」で引ける。",
    }
    notes = []
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。")
    if not hits:
        notes.append("統合表に当たる行が無い。語を減らすか、kb_search で本文も探す。どちらにも無ければ「知識ベースでは未確認」。")
    if notes:
        out["注記"] = notes
    return out


RULES_KINDS = ("", RULE_TABLE, "知識メモ", KATA, CORE)


def _core_lines(words: List[str], max_results: int) -> List[dict]:
    """90（核となる理解）の中で語を全部含む本文・表の行・見出し。"""
    out = []
    for d in load_all():
        if d["kind"] != CORE:
            continue
        hits = sorted(((-_score(u, words), u["line"], u) for u in d["units"] if _score(u, words)), key=lambda x: x[:2])
        for _, _, u in hits[:max_results]:
            it = {**_where(d, u), "形": u["type"], "本文": _snippet(u["text"], words, 300), "印": _mark_of(u)}
            out.append(it)
    return out


def rules(query: str = "", target: str = "", file: str = "", mark: str = "", max_results: int = 20,
          max_chars: int = DEFAULT_CHARS, field: str = "", kind: str = "") -> dict:
    words = _words(query)
    if mark and mark not in MARKS:
        raise KBError("印は ✅ / △ / ❓ のどれか")
    k = _kind_filter(kind) if kind else ""
    if k not in RULES_KINDS:
        raise KBError(f"kb_rules の kind は {RULE_TABLE}／知識メモ／{KATA}／{CORE} のどれか（空なら統合表と運用の型の規則）")
    max_results = _clamp(max_results, 20, MAX_RESULTS)
    max_chars = _clamp(max_chars, DEFAULT_CHARS, MAX_CHARS)
    from . import kata as _kata

    if file and not k:
        picked = select(load_all(), "", file)
        if picked and all(d["kind"] == KATA for d in picked):
            k = KATA
    if k == KATA:
        return _kata.rules_lookup(query, max_results=max_results, max_chars=max_chars)
    if k == CORE:
        if not words:
            raise KBError("核となる理解（90）から引くときは query に語を渡す")
        items = _core_lines(words, max_results)
        shown, cut = _budget(items, max_chars)
        out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
               "単位": "90_核となる理解.md の 1 行（本文・表の行・見出し）", "条件": {"語（すべて含む）": words},
               "件数": {"当たり": len(items), "表示": len(shown)}, "行": shown}
        if not items:
            out["注記"] = ["90 に当たる行が無い（90 がまだ無いこともある）。kind を外して統合表を引く。"]
        elif cut:
            out["注記"] = [f"文字数の上限（{max_chars} 字）で打ち切った。"]
        return out
    out = None
    if not file and k != "知識メモ":
        csv_rows = rule_csv_rows()
        if csv_rows is not None:
            out = _rules_from_csv(csv_rows, words, target, field, mark, max_results, max_chars)
        elif k == RULE_TABLE:
            raise KBError(f"統合表（{RULES_CSV}）が無い。kind を外すと各メモの表から引く")
    if out is None:
        out = _rules_from_memos(words, target, file, mark, max_results, max_chars, field)
    if not k and words and not mark and not field and not file:
        extra = _kata.rules_brief(words, max_results=5)
        if extra:
            out["運用の型の規則（型08。いつ・何をするか）"] = extra
        core = _core_lines(words, 3)
        if core:
            out["核となる理解（90）"] = core
    return out


def _rules_from_memos(words: List[str], target: str, file: str, mark: str, max_results: int, max_chars: int,
                      field: str) -> dict:
    t = norm(target)
    if field:
        raise KBError(f"分野で絞れるのは統合表（{RULES_CSV}）があるときだけ。file を外すか、query で絞る")
    docs = select(load_all(), "", file)
    conflicts = _conflicts(docs, 200)
    hits = []
    for d, u in rule_rows(docs):
        c = u["cells"]
        if t and t not in norm(_cell(c, "対象", "点")):
            continue
        if words and not all(w in u["n"] for w in words):
            continue
        if mark and mark not in _cell(c, "印"):
            continue
        key_hit = sum(1 for w in words if w in norm(_cell(c, "対象", "点")))
        hits.append((-key_hit, d["id"] not in ("14", "15"), d, u))
    hits.sort(key=lambda x: (x[0], x[1]))
    items = [_rule_view(d, u, CELL_CHARS, conflicts) for _, _, d, u in hits[:max_results]]
    shown, cut = _budget(items, max_chars)
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        "単位": "ルール表の 1 行（1 行 1 ルール）。14・15 の表と、各メモの「細かいルール一覧」の表から",
        "条件": {"語（すべて含む）": words, "対象": target or "指定なし", "印": mark or "指定なし", "ファイル": file or "指定なし"},
        "件数": {"当たり": len(hits), "表示": len(shown)},
        "行": shown,
        "印の読み方": "✅ 公式で確認／△ 公式の中で記述が食い違う（「食い違いの記述」に両方）／❓ 公式に記載なし（表に入れず「未確認の点」に回してある）。"
                  "「印の列なし」は、そのメモのルール一覧に印の列が無いもの。",
    }
    notes = []
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。")
    if not hits:
        notes.append("ルール表に当たる行が無い。kb_search で本文も探す。どちらにも無ければ「知識ベースでは未確認」。")
    if notes:
        out["注記"] = notes
    return out


def find_row(file: str, key: str, section: str = "", docs: Optional[List[dict]] = None) -> Optional[Tuple[dict, dict]]:
    """表の 1 行を、1 列目の値（番号・属性名など）で探す。見つからなければ None。"""
    doc = find_doc(file, docs)
    k = norm(key)
    sec = norm(section)
    for u in doc["units"]:
        if u["type"] != "表の行":
            continue
        if sec and sec not in u["nsec"]:
            continue
        first = list(u["cells"].values())[0] if u["cells"] else ""
        if norm(first) == k:
            return doc, u
    return None


def cite_line(file: str, needle: str, section: str = "") -> dict:
    """表ではなく本文（箇条書き）に書かれた根拠を、含まれる語で引く。"""
    ref = {"メモ": file, "含む語": needle}
    try:
        doc = find_doc(file)
    except KBError as ex:
        return {**ref, "確かめ": f"知識ベースを読めなかった（{ex}）。道具の中の式で計算した"}
    n, sec = norm(needle), norm(section)
    for u in doc["units"]:
        if n in u["n"] and (not sec or sec in u["nsec"]):
            return {"ファイル": doc["rel"], "節": u["section"], "行番号": u["line"], "本文": _cut(re.sub(r"[*`]", "", u["text"]), 300),
                    "印": _mark_of(u) or None, "確認日": doc["date"], "確かめ": "知識ベースの記述と合う"}
    return {**ref, "確かめ": "この記述がいまの知識ベースに見つからない。メモが直された可能性がある。道具の中の式で計算した"}


def cite(file: str, key: str, section: str = "", expect: Iterable[str] = ()) -> dict:
    """計算・点検の道具が使う数字の根拠。いまの知識ベースからその行を引いて、数字がまだ書いてあるかも確かめる。

    知識ベースが読めないときも落とさない（計算の道具は動き続ける）。
    """
    ref = {"メモ": file, "行": key}
    if section:
        ref["節"] = section
    try:
        hit = find_row(file, key, section)
    except KBError as ex:
        return {**ref, "確かめ": f"知識ベースを読めなかった（{ex}）。道具の中の値で計算した"}
    if not hit:
        return {**ref, "確かめ": "この行がいまの知識ベースに見つからない。メモが直された可能性がある。道具の中の値で計算した"}
    doc, u = hit
    text = re.sub(r"[*`]", "", u["text"])
    missing = [e for e in expect if norm(e) not in norm(text)]
    out = {"ファイル": doc["rel"], "節": u["section"], "行番号": u["line"], "行": key,
           "本文": _cut(text, 300), "印": _mark_of(u) or None, "確認日": doc["date"]}
    out["確かめ"] = (("数字は知識ベースの行と合う" if expect else "知識ベースの行を引いた") if not missing else
                   f"根拠の行に {missing} が見当たらない。メモが直された可能性がある。道具の中の値で計算した")
    return out


# ---------------- 5. ロジックツリー ----------------

_ID = re.compile(r"^([A-Z]{2,6})-([^-\s|]+)-(\d+)$")
DIRECTIONS = {
    "上": ("上", "上がった", "上がる", "上昇", "増えた", "増加", "増", "高くなった", "高い", "up", "+"),
    "下": ("下", "下がった", "下がる", "低下", "減った", "減少", "減", "低くなった", "低い", "down", "-"),
    "悪": ("悪", "悪化", "悪くなった"),
    "良": ("良", "改善", "良くなった"),
}


def _direction(text: str) -> Optional[str]:
    if not text:
        return None
    t = norm(text)
    for d, words in DIRECTIONS.items():
        if t in [norm(w) for w in words]:
            return d
    for d in ("悪", "良", "下", "上"):
        if any(norm(w) and norm(w) in t for w in DIRECTIONS[d] if len(w) >= 2):
            return d
    raise KBError(f"向きは 上（上がった・増えた）／下（下がった・減った）／悪（悪化）／良（改善）で渡す（受け取った値: {text!r}）")


def tree_rows(docs: Iterable[dict]) -> List[Tuple[dict, dict, str, str]]:
    out = []
    for d in docs:
        if d["kind"] != "ロジックツリー":
            continue
        for u in d["units"]:
            if u["type"] != "表の行" or "要因" not in [norm(h) for h in u["header"]]:
                continue
            first = re.sub(r"[`*]", "", list(u["cells"].values())[0]).strip()
            m = _ID.match(first)
            if m:
                out.append((d, u, m.group(1), m.group(2)))
    return out


def _metric_index(docs: List[dict]) -> List[dict]:
    """指標の名前 → 略号。各ツリーの大見出し（「2. 表示回数（IMP）」など）と、その節の表の番号から作る。"""
    rows = tree_rows(docs)
    out = []
    for d in docs:
        if d["kind"] != "ロジックツリー":
            continue
        legend = {}
        for ln in d["lines"][:25]:  # 冒頭の凡例（「番号の略号」「対象の指標」の行）だけを見る
            if "略号" in ln:
                for code, name in re.findall(r"([A-Z]{2,6})\s*[＝=]\s*([^／/。\n]+)", ln):
                    if len(name.strip()) <= 30:
                        legend[code] = name.strip()
            if "対象の指標" in ln:
                for name, code in re.findall(r"[①-⑳]\s*([^①-⑳（）]+?)（([A-Z]{2,6})）", ln):
                    legend.setdefault(code, name.strip())
        for s in d["sections"]:
            if s["level"] != 2:
                continue
            codes = sorted({c for dd, u, c, _ in rows if dd is d and s["line"] <= u["line"] <= s["end"]})
            if not codes:
                continue
            names = [re.sub(r"^\d+\.\s*", "", s["title"])] + [legend[c] for c in codes if c in legend]
            out.append({"doc": d, "section": s, "codes": codes, "names": names})
    return out


def _glossary_names(query: str, docs: List[dict]) -> List[str]:
    """用語集で、その語の別名・略語を引く（指標の別名で引けるように）。"""
    q = norm(query)
    extra = []
    for d, u in _glossary_rows(docs):
        c = u["cells"]
        keys = [_cell(c, "用語"), _cell(c, "英語"), _cell(c, "略語・別名・旧称", "略語")]
        parts = [norm(p) for k in keys for p in re.split(r"[／/、,（）()]", k) if norm(p) and norm(p) not in ("—", "-")]
        if q in parts:
            extra += [p for p in parts if len(p) >= 2]
    return extra


def logic_tree(metric: str = "", direction: str = "", id: str = "", max_results: int = 15,
               max_chars: int = 9000, cell_chars: int = 300) -> dict:
    max_results = _clamp(max_results, 15, MAX_RESULTS)
    max_chars = _clamp(max_chars, 9000, MAX_CHARS)
    cell_chars = _clamp(cell_chars, 300, 2000)
    docs = load_all()
    rows = tree_rows(docs)
    head = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
            "単位": "ロジックツリーの要因 1 行（番号・分類・要因・なぜ・確かめ方・打ち手・副作用・根拠）"}
    if id:
        want = re.sub(r"\s+", "", unicodedata.normalize("NFKC", id)).upper()
        picked = [(d, u) for d, u, c, dr in rows
                  if re.sub(r"[`*]", "", list(u["cells"].values())[0]).strip().upper() == want
                  or re.sub(r"[`*]", "", list(u["cells"].values())[0]).strip().upper().startswith(want + "-")]
        if not picked:
            raise KBError(f"番号 {id!r} の行が見つからない（例: CPC-上-07）。metric と direction で探す")
        items = [{**_tree_view(u, cell_chars), **_where(d, u)} for d, u in picked[:max_results]]
        shown, cut = _budget(items, max_chars)
        out = {**head, "条件": {"番号": id}, "件数": {"当たり": len(picked), "表示": len(shown)}, "行": shown}
        if cut:
            out["注記"] = [f"文字数の上限（{max_chars} 字）で打ち切った。"]
        return out
    if not metric:
        idx = _metric_index(docs)
        return {**head, "条件": {}, "指標の一覧": [
            {"指標": m["names"][0], "略号": m["codes"], "ファイル": m["doc"]["rel"], "向き": sorted({dr for _, _, c, dr in rows if c in m["codes"]})}
            for m in idx], "使い方": "metric に指標の名前か略号、direction に 上／下（STQ は 悪／良）を渡す。番号（例: CPC-上-07）でも引ける。"}
    want_dir = _direction(direction)
    idx = _metric_index(docs)
    q = norm(metric)
    names = {q} | set(_glossary_names(metric, docs))
    matched = []
    for m in idx:
        codes_n = [norm(c) for c in m["codes"]]
        hay = [norm(n) for n in m["names"]]
        if any(n in codes_n for n in names) or any(n in h for n in names for h in hay if len(n) >= 2):
            matched.append(m)
    if not matched:
        raise KBError(f"指標 {metric!r} に当たるツリーが無い。metric を空にして呼ぶと、引ける指標の一覧が返る")
    items, total, dirs = [], 0, set()
    for m in matched:
        for d, u, c, dr in rows:
            if d is m["doc"] and c in m["codes"] and m["section"]["line"] <= u["line"] <= m["section"]["end"]:
                dirs.add(dr)
                if want_dir and dr != want_dir:
                    continue
                total += 1
                if len(items) < max_results:
                    items.append({**_tree_view(u, cell_chars), **_where(d, u)})
    first_steps = []
    for m in matched:
        for s in m["doc"]["sections"]:
            if m["section"]["line"] < s["line"] <= m["section"]["end"] and "切り分ける順番" in s["title"]:
                body = "\n".join(m["doc"]["lines"][s["line"]:s["end"]]).strip()
                first_steps.append({"ファイル": m["doc"]["rel"], "節": s["path"], "行": s["line"], "本文": _cut(body, 1200)})
                break
    shown, cut = _budget(items, max_chars)
    out = {
        **head,
        "条件": {"指標": metric, "向き": want_dir or "指定なし（両方）"},
        "当たった指標": [{"指標": m["names"][0], "略号": m["codes"], "ファイル": m["doc"]["rel"], "確認日": m["doc"]["date"]} for m in matched],
        "件数": {"当たり": total, "表示": len(shown), "この指標の向き": sorted(dirs)},
        "まず切り分ける順番": first_steps,
        "行": shown,
        "根拠の種類の読み方": "`公式` Google の資料／`データあり` 数字の裏づけあり／`経験則` 実務家の主張（仮説として扱う）／`推測` メモを書いた AI の整理。",
    }
    notes = []
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。番号で 1 行ずつ引ける。")
    if want_dir and not total:
        notes.append(f"この指標には向き「{want_dir}」の行が無い。ある向き: {sorted(dirs)}")
    if notes:
        out["注記"] = notes
    return out


def _tree_view(u: dict, cell_chars: int) -> dict:
    v = _row_view(u, cell_chars)
    ev = _evidence(u["cells"].get("根拠", ""))
    if ev:
        v["根拠の種類"] = ev
    if "経験則" in ev or "推測" in ev:
        v["扱い"] = "根拠に経験則・推測を含む。仮説として扱う"
    return v


# ---------------- 6. 用語集 ----------------

def _glossary_rows(docs: Iterable[dict]) -> List[Tuple[dict, dict]]:
    out = []
    for d in docs:
        if "用語集" not in d["title"]:
            continue
        for u in d["units"]:
            if u["type"] == "表の行" and ("用語" in [norm(h) for h in u["header"]] or norm(u["header"][0]).startswith("旧称")):
                out.append((d, u))
    return out


def glossary(term: str, max_results: int = 10, max_chars: int = DEFAULT_CHARS) -> dict:
    q = norm(term)
    if not q:
        raise KBError("引く用語を渡す（旧称・略語・英語でも引ける）")
    max_results = _clamp(max_results, 10, MAX_RESULTS)
    docs = load_all()
    rows = _glossary_rows(docs)
    if not rows:
        raise KBError("用語集（題名に「用語集」を含むメモ）が知識ベースに見つからない")
    hits = []
    for d, u in rows:
        c = u["cells"]
        keys = [_cell(c, "用語"), _cell(c, "英語"), _cell(c, "略語・別名・旧称", "略語"), _cell(c, "旧称・別の表記", "旧称"),
                _cell(c, "今の呼び名")]
        parts = [norm(p) for k in keys for p in re.split(r"[／/、,]|（|）|\(|\)", k) if norm(p)]
        if q in parts or q == norm(keys[0]):
            score = 3
        elif any(q in p for p in parts):
            score = 2
        elif q in u["n"]:
            score = 1
        else:
            continue
        hits.append((-score, u["line"], d, u))
    hits.sort(key=lambda x: (x[0], x[1]))
    items = []
    for sc, _, d, u in hits[:max_results]:
        kind = "名前が変わった用語" if norm(u["header"][0]).startswith("旧称") else "用語"
        items.append({"形": kind, "当たり方": {-3: "名前・略語・旧称が一致", -2: "名前・略語・旧称の一部", -1: "意味の文の中"}[sc],
                      **_row_view(u, CELL_CHARS), "印": _mark_of(u), **_where(d, u)})
    shown, cut = _budget(items, _clamp(max_chars, DEFAULT_CHARS, MAX_CHARS))
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": "用語集の 1 行（用語・英語・略語や旧称・意味・間違えやすい点・くわしいメモ）",
           "条件": {"用語": term}, "件数": {"当たり": len(hits), "表示": len(shown)}, "行": shown,
           "印の読み方": "意味の末尾の ※ は公式ページを取り直していない一般知識、△ は記述の食い違い（用語集の凡例）。"}
    notes = []
    if cut:
        notes.append("文字数の上限で打ち切った。")
    if not hits:
        notes.append("用語集に無い。kb_search で本文を探す。")
    if notes:
        out["注記"] = notes
    return out


# ---------------- 7. Tips ----------------

def tip_rows(docs: Iterable[dict]) -> List[Tuple[dict, dict]]:
    out = []
    for d in docs:
        if d["kind"] != "Tips":
            continue
        for u in d["units"]:
            if u["type"] == "表の行" and any(norm(h).startswith("tip") for h in u["header"]):
                out.append((d, u))
    return out


def tips(query: str = "", scene: str = "", file: str = "", max_results: int = 10, max_chars: int = DEFAULT_CHARS) -> dict:
    words = _words(query)
    sc = norm(scene)
    if not words and not sc:
        raise KBError("query（語）か scene（場面）のどちらかを渡す")
    max_results = _clamp(max_results, 10, MAX_RESULTS)
    docs = select(load_all(), "Tips", file)
    hits = []
    for d, u in tip_rows(docs):
        if sc and sc not in norm(_cell(u["cells"], "場面")):
            continue
        if words and not all(w in u["n"] for w in words):
            continue
        hits.append((d, u))
    items = []
    for d, u in hits[:max_results]:
        items.append({**_row_view(u, CELL_CHARS), "扱い": "仮説", **_where(d, u)})
    shown, cut = _budget(items, _clamp(max_chars, DEFAULT_CHARS, MAX_CHARS))
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": "Tips の 1 行（場面・Tip・根拠の種類・誰の主張か・時期・条件と反対意見）",
           "必ず伝えること": TIPS_CAUTION,
           "条件": {"語（すべて含む）": words, "場面": scene or "指定なし", "ファイル": file or "指定なし"},
           "件数": {"当たり": len(hits), "表示": len(shown)}, "行": shown}
    if cut:
        out["注記"] = ["文字数の上限で打ち切った。"]
    return out


# ---------------- 8. 主張を照らす ----------------

_TERM = re.compile(r"[一-龥々〆ヵヶァ-ヴーA-Za-z0-9＋+][一-龥々〆ヵヶァ-ヴーA-Za-z0-9＋+\-.・]*")
_STOP = {"こと", "もの", "ため", "場合", "以上", "以下", "未満", "とき", "など", "よう", "これ", "それ", "必要", "可能", "設定", "使う"}


def claim_terms(claim: str) -> Tuple[List[str], List[str]]:
    t = unicodedata.normalize("NFKC", claim)
    terms = []
    nums = [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t)]
    for m in _TERM.findall(re.sub(r"\d[\d,]*(?:\.\d+)?", " ", t)):
        m = m.strip("-.・")
        for part in re.split(r"(?<=[一-龥々])(?=[ァ-ヴA-Za-z])|(?<=[ァ-ヴー])(?=[一-龥々])", m):
            if len(part) >= 2 and part not in _STOP:
                terms.append(part)
        if len(m) >= 2 and m not in terms and m not in _STOP and len(m) <= 20:
            terms.append(m)
    seen, out = set(), []
    for x in terms:
        n = norm(x)
        if n not in seen:
            seen.add(n)
            out.append(x)
    return out, sorted(set(nums), key=nums.index)


def check_claim(claim: str, max_results: int = 15, max_chars: int = 9000) -> dict:
    claim = str(claim or "").strip()
    if not claim:
        raise KBError("照らしたい文（主張）を渡す")
    terms, nums = claim_terms(claim)
    if not terms:
        raise KBError("文から探す語を取り出せなかった。名詞を含む文にするか、kb_search で語を指定する")
    max_results = _clamp(max_results, 15, MAX_RESULTS)
    max_chars = _clamp(max_chars, 9000, MAX_CHARS)
    docs = load_all()
    nterms = [(t, [norm(t)] + [x for x in _glossary_names(t, docs) if x != norm(t)]) for t in terms]
    need = 1 if len(terms) == 1 else 2
    core_group = "核となる理解・運用の型（体系）"
    groups = {core_group: [], "ルール表": [], "知識メモ・運用の型": [], "ロジックツリー": [], "Tips（仮説）": []}
    rule_ids = {id(u) for _, u in rule_rows(docs)}
    for d in docs:
        if d["kind"] == QUIZ:
            continue  # 理解度テストは quiz.jsonl の「よくある誤答」で照らす（下の「よくある誤答に近い」）
        for u in d["units"]:
            if u["type"] == "見出し":
                continue
            got = [t for t, alts in nterms if any(a in u["n"] for a in alts)]
            if len(got) < need:
                continue
            # 長い語に含まれる短い語（「検索広告」と「レスポンシブ検索広告」など）は半分に数える
            score = sum(0.5 if any(norm(t) != norm(o) and norm(t) in norm(o) for o in got) else 1.0 for t in got)
            same_num = [n for n in nums if re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", unicodedata.normalize("NFKC", u["text"]).replace(",", ""))]
            score += 2 * len(same_num)
            if id(u) in rule_ids:
                g, score = "ルール表", score * 1.5
            elif d["kind"] == "Tips":
                g = "Tips（仮説）"
            elif d["kind"] == "ロジックツリー":
                g = "ロジックツリー"
            elif d["kind"] in (CORE, KATA):
                g = core_group
            else:
                g = "知識メモ・運用の型"
            groups[g].append((score, d, u, got, same_num))
    out_groups, total = {}, 0
    quota = {core_group: max(3, max_results // 2), "ルール表": max_results, "知識メモ・運用の型": max_results,
             "ロジックツリー": max(3, max_results // 3), "Tips（仮説）": max(3, max_results // 3)}
    flat = []
    for g, rows in groups.items():
        rows.sort(key=lambda x: (-x[0], x[1]["rel"], x[2]["line"]))
        total += len(rows)
        items = []
        for score, d, u, got, same_num in rows[: quota[g]]:
            it = {**_where(d, u), "合った語": got}
            if same_num:
                it["同じ数字"] = same_num
            if u["type"] == "表の行":
                it["行の中身"] = _row_view(u, 250)
            else:
                it["本文"] = _cut(re.sub(r"[*`]", "", u["text"]), 300)
            it["印"] = _mark_of(u)
            ev = _evidence_of(u)
            if ev:
                it["根拠の種類"] = ev
            if g == "Tips（仮説）":
                it["扱い"] = "仮説"
            items.append(it)
        flat.append((g, items))
    used = 0
    for g, items in flat:
        keep = []
        for it in items:
            size = len(json.dumps(it, ensure_ascii=False))
            if keep and used + size > max_chars:
                break
            keep.append(it)
            used += size
        out_groups[g] = keep
    from . import quiz as _quiz

    near = _quiz.near_misconceptions(claim, max_results=3)
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        "単位": "主張に関係しそうな、ルール表の行・本文・ツリーの行・Tips（と、理解度テストのよくある誤答）",
    }
    if near:
        out["よくある誤答に近い"] = {
            "知らせ": "この主張は、理解度テスト（quiz.jsonl）の「よくある誤答」に近い。下の正解と根拠を先に読む。",
            "行": near}
    return {
        **out,
        "主張": claim,
        "使った語": terms,
        "主張の中の数字": nums,
        "件数": {"当たり（全部）": total, **{g: len(v) for g, v in out_groups.items()}},
        "関係しそうな記述": out_groups,
        "この道具がしないこと": "主張が正しいかは判定しない。並べた記述と主張を見比べるのは Claude。"
                          "どこにも無い・❓ しか無いなら「知識ベースでは未確認」と答える。Tips は仮説。",
    }
