"""運用の型（知識ベースの 運用の型/）を引く。読むだけで、書き換えない。

- 規則は 型08（判断の規則集）の表、しきい値は 型91（付録B）、式は 型90（付録A）、状態は 型06（決定木）と
  型08 の「使い方」の表、裁定は 型92（付録C）、未確認は 型93（付録D）から、呼ぶたびに読む（kb の索引を使う）。
- 列は見出しの名前で探す（列の順や行の数を決め打ちにしない）。ファイルは点検中で、行が増減する。
- 判定はしない。状態の判定は、問いと枝を返すだけ。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

from . import kb

DEFAULT_RESULTS = 20
CELL_CHARS = 1500
# 規則の表の列（見出しを norm した形 → 返すときの名前）
RULE_COLS = {"id": "ID", "もし": "条件", "なら": "行動", "理由": "理由", "例外": "例外", "根拠": "根拠の種類",
             "参照": "参照", "状態": "使う状態"}
SUISOKU_NOTE = "根拠に〔推論〕を含む。公式の数字ではない目安として伝える（出典のある公式の値ではない）。"
KATA_KINDS = {
    "規則": ("規則", "ルール", "rule", "rules"),
    "しきい値": ("しきい値", "閾値", "閾", "threshold"),
    "式": ("式", "formula"),
    "状態": ("状態", "状態の判定", "決定木", "判定"),
    "裁定": ("裁定", "食い違い", "食い違いと裁定"),
    "未確認": ("未確認", "未確認の点"),
}
_KIND_ALIAS = {kb.norm(a): k for k, al in KATA_KINDS.items() for a in al}


def _clean(v: str) -> str:
    return re.sub(r"\*\*|`", "", str(v)).strip()


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or ""))


def _hn(u: dict) -> List[str]:
    return [kb.norm(h) for h in u.get("header") or []]


def _first(u: dict) -> str:
    return _clean(list(u["cells"].values())[0]) if u.get("cells") else ""


def _doc(nn: str) -> dict:
    docs = kb.load_all()
    hits = kb.resolve(f"型{nn}", docs)
    if not hits:
        raise kb.KBError(f"運用の型/{nn}_*.md が知識ベースに無い（運用の型/ フォルダを確かめる）")
    if len(hits) > 1:
        raise kb.KBAmbiguous(f"型{nn}", hits)
    return hits[0]


def _loc(d: dict, u: dict) -> dict:
    return {"ファイル": d["rel"], "節": u["section"], "行": u["line"], "確認日": d["date"]}


def _tags(text: str) -> List[str]:
    seen = []
    for t in re.findall(r"〔(.+?)〕", str(text)):
        if t not in seen:
            seen.append(t)
    return seen


# ---------------- 番号の読み取り ----------------

def _rule_id(s: str) -> Optional[str]:
    m = re.match(r"^規([^\s\d\-])\s*-\s*0*(\d+)$", _nfkc(s).strip())
    return f"規{m.group(1)}-{int(m.group(2)):02d}" if m else None


def _thr_id(n) -> str:
    return f"閾{int(n):02d}"


def _formula_id(n) -> str:
    return f"式 {int(n)}"


def parse_query(query: str) -> dict:
    """query から番号（規換-15、規換、状4、閾08、式 4、裁定 3）と残りの語を取り出す。"""
    t = _nfkc(query)
    out: Dict[str, List[str]] = {"rule_ids": [], "cats": [], "states": [], "thr": [], "formulas": [], "rulings": []}

    def take(pattern: str, key: str, fn):
        nonlocal t
        for m in re.finditer(pattern, t):
            v = fn(m)
            if v not in out[key]:
                out[key].append(v)
        t = re.sub(pattern, " ", t)

    take(r"規([^\s\d\-])\s*-\s*(\d+)", "rule_ids", lambda m: f"規{m.group(1)}-{int(m.group(2)):02d}")
    take(r"閾\s*(\d+)", "thr", lambda m: _thr_id(m.group(1)))
    take(r"式\s*(\d+)", "formulas", lambda m: _formula_id(m.group(1)))
    take(r"裁定\s*(\d+)", "rulings", lambda m: f"裁定 {int(m.group(1))}")
    take(r"状\s*(\d)(?!\d)", "states", lambda m: f"状{m.group(1)}")
    rest = []
    for tok in t.split():
        m = re.fullmatch(r"規([^\s\d\-])", tok)
        if m:
            if tok not in out["cats"]:
                out["cats"].append(tok)
        else:
            rest.append(tok)
    out["words"] = kb._words(" ".join(rest))
    out["rest"] = " ".join(rest)
    return out


def _refs_in(text: str) -> Tuple[List[str], List[str]]:
    t = _nfkc(text)
    thr = []
    for n in re.findall(r"閾\s*(\d+)", t):
        if _thr_id(n) not in thr:
            thr.append(_thr_id(n))
    forms = []
    for grp in re.findall(r"式\s*(\d+(?:\s*[・、,]\s*\d+)*)", t):
        for n in re.findall(r"\d+", grp):
            if _formula_id(n) not in forms:
                forms.append(_formula_id(n))
    return thr, forms


# ---------------- 型08: 規則 ----------------

def _is_rule_table(u: dict) -> bool:
    h = _hn(u)
    return "id" in h and "もし" in h and "なら" in h


def all_rules(doc: dict) -> List[dict]:
    return [u for u in doc["units"] if u["type"] == "表の行" and _is_rule_table(u) and _rule_id(_first(u))]


def categories(doc: dict) -> List[dict]:
    """型08 の「接頭辞」の表（分類と優先の順位）。"""
    out = []
    for u in doc["units"]:
        if u["type"] == "表の行" and "接頭辞" in _hn(u):
            out.append({("優先の順位" if kb.norm(k).startswith("優先") else k): _clean(v) for k, v in u["cells"].items()})
    return out


def state_rows(doc08: dict, state: str = "") -> List[dict]:
    """型08 の「使い方」の表（状態ごとの 主に引く分類・触ってはいけないもの）。"""
    out = []
    for u in doc08["units"]:
        h = _hn(u)
        if u["type"] == "表の行" and "状態" in h and any(x.startswith("主に引く") for x in h):
            if state and _first(u) != state:
                continue
            out.append({**{k: _clean(v) for k, v in u["cells"].items()}, **_loc(doc08, u)})
    return out


def _rule_view(d: dict, u: dict, cell_chars: int) -> dict:
    row: Dict[str, object] = {}
    for h, v in u["cells"].items():
        row[RULE_COLS.get(kb.norm(h), h)] = kb._cut(_clean(v), cell_chars)
    row["分類"] = u["section"].split(" > ")[-1]
    ev = next((v for h, v in u["cells"].items() if kb.norm(h) == "根拠"), "")
    row["根拠の種類の一覧"] = _tags(ev)
    thr, forms = _refs_in(u["text"])
    row["指すしきい値"] = thr
    row["指す式"] = forms
    row.update(_loc(d, u))
    return row


def _state_match(u: dict, state: str) -> bool:
    cell = next((v for h, v in u["cells"].items() if kb.norm(h) == "状態"), "")
    return bool(re.search(rf"{re.escape(state)}(?!\d)", _nfkc(cell)))


def _priority(doc: dict) -> Dict[str, int]:
    out = {}
    for c in categories(doc):
        pre = c.get("接頭辞", "")
        m = re.search(r"\d+", c.get("優先の順位", ""))
        out[pre] = int(m.group()) if m else 99
    return out


def _thr_brief(t: dict) -> dict:
    keep = {k: t[k] for k in ("番号", "事柄", "値", "根拠の種類") if k in t}
    keep["rules.csv の id"] = t.get("rules.csv の対応")
    if t.get("注意"):
        keep["注意"] = t["注意"]
    keep["ファイル"], keep["行"] = t["ファイル"], t["行"]
    return keep


def _formula_brief(f: dict) -> dict:
    keep = {k: f[k] for k in ("番号", "式", "根拠") if k in f}
    keep["ファイル"], keep["行"] = f["ファイル"], f["行"]
    return keep


def rules_lookup(query: str = "", state: str = "", max_results: int = DEFAULT_RESULTS, max_chars: int = 9000,
                 cell_chars: int = CELL_CHARS) -> dict:
    p = parse_query(query)
    states = p["states"] + ([s for s in parse_query(state)["states"] if s not in p["states"]] if state else [])
    if state and not parse_query(state)["states"]:
        raise kb.KBError(f"状態は 状0〜状8 の形で渡す（受け取った値: {state!r}）")
    max_results = kb._clamp(max_results, DEFAULT_RESULTS, kb.MAX_RESULTS)
    max_chars = kb._clamp(max_chars, 9000, kb.MAX_CHARS)
    cell_chars = kb._clamp(cell_chars, CELL_CHARS, 5000)
    doc = _doc("08")
    rows = all_rules(doc)
    prio = _priority(doc)
    hits = []
    for order, u in enumerate(rows):
        rid = _rule_id(_first(u))
        if p["rule_ids"] and rid not in p["rule_ids"]:
            continue
        if p["cats"] and not any(rid.startswith(c + "-") for c in p["cats"]):
            continue
        if states and not any(_state_match(u, s) for s in states):
            continue
        if p["words"] and not all(w in u["n"] for w in p["words"]):
            continue
        key = (p["rule_ids"].index(rid),) if p["rule_ids"] else (prio.get(rid.split("-")[0], 99), order)
        hits.append((key, u))
    hits.sort(key=lambda x: x[0])
    items = [_rule_view(doc, u, cell_chars) for _, u in hits[:max_results]]
    shown, cut = kb._budget(items, max_chars)
    thr_idx, formula_idx = None, None
    thr_used = [t for it in shown for t in it["指すしきい値"]]
    form_used = [f for it in shown for f in it["指す式"]]
    contents: Dict[str, object] = {}
    notes: List[str] = []
    if thr_used:
        try:
            thr_idx = threshold_index()
            contents["しきい値の中身（型91）"] = {t: _thr_brief(thr_idx[t]) for t in dict.fromkeys(thr_used) if t in thr_idx}
        except kb.KBError as ex:
            notes.append(f"しきい値の中身を添えられなかった（{ex}）")
    if form_used:
        try:
            formula_idx = formula_index()
            contents["式の中身（型90）"] = {f: _formula_brief(formula_idx[f]) for f in dict.fromkeys(form_used) if f in formula_idx}
        except kb.KBError as ex:
            notes.append(f"式の中身を添えられなかった（{ex}）")
    out = {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし（いまのファイル）",
        "単位": "型08 の規則 1 行（ID・条件〔もし〕・行動〔なら〕・理由・例外・根拠の種類・参照・使う状態）",
        "条件": {"規則の番号": p["rule_ids"] or "指定なし", "分類": p["cats"] or "指定なし", "状態": states or "指定なし",
               "語（すべて含む）": p["words"]},
        "件数": {"当たり": len(hits), "表示": len(shown), "型08 の規則の数": len(rows)},
    }
    if states:
        try:
            out["状態の使い方（型08 の使い方の表）"] = [r for s in states for r in state_rows(doc, s)]
        except kb.KBError:
            pass
    out["行"] = shown
    out.update(contents)
    out["読み方"] = ("規則は「もし（条件）なら（行動）」。状態 → 分類 → 規則 の順に引く。ぶつかったら 門 → 依存関係 → 先行時間 → 点数"
                  "（kb_read(file='型08', section='規則どうしが衝突したときの優先')）。〔推論〕のしきい値は公式の数字ではない目安。")
    if p["rule_ids"]:
        found = {_rule_id(_first(u)) for _, u in hits}
        missing = [r for r in p["rule_ids"] if r not in found]
        if missing:
            notes.append(f"型08 に見つからない番号: {missing}。分類（規換 など）で一覧を引いて確かめる。")
    if states:
        allst = sum(1 for u in rows if "全状態" in _nfkc(next((v for h, v in u["cells"].items() if kb.norm(h) == "状態"), "")))
        notes.append(f"使う状態が「全状態」の規則（主に規記。{allst} 個）は含めていない。どの状態でも 規記-01・02 は同時に使う。")
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。番号か語で絞る。")
    if not hits:
        notes.append("当たる規則が無い。語を減らす、分類（規計・規定・規時・規囲・規材・規束・規換・規拡・規守・規記）で引く。")
    if notes:
        out["注記"] = notes
    return out


def rules_brief(words: List[str], max_results: int = 5) -> List[dict]:
    """kb_rules に添える短い形。型08 が無ければ空。"""
    try:
        doc = _doc("08")
    except kb.KBError:
        return []
    out = []
    for u in all_rules(doc):
        if words and all(w in u["n"] for w in words):
            v = _rule_view(doc, u, 220)
            out.append({k: v.get(k) for k in ("ID", "条件", "行動", "根拠の種類", "使う状態", "ファイル", "行")})
            if len(out) >= max_results:
                break
    return out


# ---------------- 型91: しきい値 ----------------

_RID = re.compile(r"R-[^\s`、,〜~|\-]+-\d+")


def _thr_tables(doc: dict):
    numbered: Dict[str, dict] = {}
    unnumbered: List[dict] = []
    mapping: Dict[str, List[dict]] = {}
    for u in doc["units"]:
        if u["type"] != "表の行":
            continue
        h = _hn(u)
        first = _first(u)
        if "値" in h and "根拠の種類" in h:
            m = re.match(r"^閾\s*(\d+)$", _nfkc(first))
            if m:
                numbered.setdefault(_thr_id(m.group(1)), u)
            elif not first.startswith("閾"):
                unnumbered.append(u)
        if any(x.startswith("ルール表のid") for x in h):
            for n in re.findall(r"閾\s*(\d+)", _nfkc(first)):
                mapping.setdefault(_thr_id(n), []).append(u)
    return numbered, unnumbered, mapping


def _csv_index() -> Dict[str, dict]:
    try:
        rows = kb.rule_csv_rows()
    except kb.KBError:
        rows = None
    return {r["id"]: r for r in rows or []}


def _thr_view(d: dict, u: dict, mapping: Dict[str, List[dict]], csv: Dict[str, dict], cell_chars: int) -> dict:
    v: Dict[str, object] = {k: kb._cut(_clean(x), cell_chars) for k, x in u["cells"].items()}
    first = _first(u)
    m = re.match(r"^閾\s*(\d+)$", _nfkc(first))
    tid = _thr_id(m.group(1)) if m else None
    ev = next((x for h, x in u["cells"].items() if kb.norm(h) == "根拠の種類"), "")
    v["根拠の種類の一覧"] = _tags(ev)
    if tid:
        links = []
        for mu in mapping.get(tid, []):
            idcell = next((x for h, x in mu["cells"].items() if kb.norm(h).startswith("ルール表のid")), "")
            markcell = next((x for h, x in mu["cells"].items() if kb.norm(h).startswith("ルール表の印")), "")
            for rid in _RID.findall(_nfkc(idcell)):
                r = csv.get(rid)
                links.append({"id": rid, "印（91 §7）": _clean(markcell) or None,
                              "印（rules.csv）": r["印"] if r else "（rules.csv に無い）",
                              "ルール": kb._cut(r["ルール"], 200) if r else None,
                              "書かれた形": kb._cut(_clean(idcell), 120), "91 の行": mu["line"]})
        v["rules.csv の対応"] = links or "（91 §7 の対応表に、この番号の行は無い）"
    else:
        v["番号"] = None
        v["扱い"] = "番号の無いしきい値（91 §3 など。章の中で使われている値）"
        v["rules.csv の対応"] = "（番号が無いので 91 §7 の対応表には載らない）"
    if "推論" in ev:
        v["注意"] = SUISOKU_NOTE
    v.update(_loc(d, u))
    return v


def threshold_index(cell_chars: int = CELL_CHARS) -> Dict[str, dict]:
    doc = _doc("91")
    numbered, _, mapping = _thr_tables(doc)
    csv = _csv_index()
    return {t: _thr_view(doc, u, mapping, csv, cell_chars) for t, u in numbered.items()}


def thresholds_lookup(query: str = "", max_results: int = DEFAULT_RESULTS, max_chars: int = 9000,
                      cell_chars: int = 600) -> dict:
    p = parse_query(query)
    max_results = kb._clamp(max_results, DEFAULT_RESULTS, kb.MAX_RESULTS)
    max_chars = kb._clamp(max_chars, 9000, kb.MAX_CHARS)
    doc = _doc("91")
    numbered, unnumbered, mapping = _thr_tables(doc)
    csv = _csv_index()
    picked: List[dict] = []
    if p["thr"]:
        picked = [numbered[t] for t in p["thr"] if t in numbered]
    if p["words"]:
        pool = picked or list(numbered.values()) + unnumbered
        picked = [u for u in pool if all(w in u["n"] for w in p["words"])]
    elif not p["thr"]:
        picked = list(numbered.values())
    items = [_thr_view(doc, u, mapping, csv, kb._clamp(cell_chars, 600, 5000)) for u in picked[:max_results]]
    shown, cut = kb._budget(items, max_chars)
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": "型91（付録B しきい値の一覧）の 1 行。根拠の種類と、対応する rules.csv の id（91 §7）つき",
           "条件": {"番号": p["thr"] or "指定なし", "語（すべて含む）": p["words"]},
           "件数": {"当たり": len(picked), "表示": len(shown), "番号つきのしきい値の数": len(numbered)},
           "行": shown,
           "読み方": "〔公式〕Google の資料／〔経験則〕実務家の目安／〔推論〕この体系が式や仕組みから置いた値（公式の数字ではない）。"
                   "印（✅△❓）は rules.csv の側が正。"}
    notes = []
    missing = [t for t in p["thr"] if t not in numbered]
    if missing:
        notes.append(f"型91 §1 に無い番号: {missing}")
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。")
    if not picked:
        notes.append("当たるしきい値が無い。語を減らすか、kb_search(kind='運用の型（体系）') で本文を探す。")
    if notes:
        out["注記"] = notes
    return out


# ---------------- 型90: 式 ----------------

def formula_index(cell_chars: int = CELL_CHARS) -> Dict[str, dict]:
    doc = _doc("90")
    out: Dict[str, dict] = {}
    for u in doc["units"]:
        if u["type"] != "表の行":
            continue
        m = re.match(r"^式\s*(\d+)$", _nfkc(_first(u)))
        if m and _formula_id(m.group(1)) not in out:
            v = {k: kb._cut(_clean(x), cell_chars) for k, x in u["cells"].items()}
            v["番号"] = _formula_id(m.group(1))
            v["_n"] = u["n"]
            v.update(_loc(doc, u))
            out[v["番号"]] = v
    return out


def formulas_lookup(query: str = "", max_results: int = DEFAULT_RESULTS, max_chars: int = 9000) -> dict:
    p = parse_query(query)
    max_results = kb._clamp(max_results, DEFAULT_RESULTS, kb.MAX_RESULTS)
    idx = formula_index()
    picked = [idx[f] for f in p["formulas"] if f in idx] if p["formulas"] else list(idx.values())
    if p["words"]:
        picked = [f for f in picked if all(w in f["_n"] for w in p["words"])]
    items = [{k: v for k, v in f.items() if k != "_n"} for f in picked[:max_results]]
    shown, cut = kb._budget(items, kb._clamp(max_chars, 9000, kb.MAX_CHARS))
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": "型90（付録A 式の一覧）の 1 行（式・前提と仮定・数値例・使う章・根拠）",
           "条件": {"番号": p["formulas"] or "指定なし", "語（すべて含む）": p["words"]},
           "件数": {"当たり": len(picked), "表示": len(shown), "式の数": len(idx)}, "行": shown,
           "読み方": "記号（S・N・BE・β・W・P・L など）は kb_read(file='型90', section='記号の表')。計算は小さな道具で行い、暗算しない。"}
    notes = []
    missing = [f for f in p["formulas"] if f not in idx]
    if missing:
        notes.append(f"型90 に無い番号: {missing}")
    if cut:
        notes.append("文字数の上限で打ち切った。")
    if not picked:
        notes.append("当たる式が無い。")
    if notes:
        out["注記"] = notes
    return out


# ---------------- 型06: 状態の判定 ----------------

def _section_lines(doc: dict, title_has: str) -> Tuple[Optional[dict], List[str]]:
    for s in doc["sections"]:
        if title_has in s["title"]:
            return s, doc["lines"][s["line"]:s["end"]]
    return None, []


def _tree(doc06: dict) -> Tuple[Optional[dict], str, List[dict]]:
    sec, lines = _section_lines(doc06, "決定木")
    if not sec:
        return None, "", []
    block, inside = [], False
    for ln in lines:
        if ln.strip().startswith("```"):
            if inside:
                break
            inside = True
            continue
        if inside:
            block.append(ln)
    qs: List[dict] = []
    for ln in block:
        m = re.match(r"^\s*問\s*(\d+)\s+(.*)$", ln)
        if m:
            qs.append({"問": f"問 {m.group(1)}", "問い": m.group(2).strip(), "枝": [], "行き先": []})
        elif qs and ln.strip():
            qs[-1]["枝"].append(ln.strip())
    for q in qs:
        for b in q["枝"]:
            for st in re.findall(r"【(状\d)", b):
                if st not in q["行き先"]:
                    q["行き先"].append(st)
    return sec, "\n".join(block), qs


def state_guide(state: str = "") -> dict:
    st = parse_query(state)["states"] if state else []
    if state and not st:
        raise kb.KBError(f"状態は 状0〜状8 の形で渡す（受け取った値: {state!r}）")
    doc06 = _doc("06")
    sec, block, qs = _tree(doc06)
    if not sec:
        raise kb.KBError("型06 に「決定木」の節が見つからない（見出しが変わった可能性がある）")
    crit = [{"問": re.match(r"^問\s*\d+", s["title"]).group(0), "見出し": s["title"], "行": s["line"],
             "読むには": f"kb_read(file='型06', section='{s['title']}')"}
            for s in doc06["sections"] if re.match(r"^問\s*\d+", s["title"])]
    try:
        doc08 = _doc("08")
        use = [r for s in (st or [""]) for r in state_rows(doc08, s)]
    except kb.KBError:
        use = []
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": "型06 の決定木の問い（上から順に答え、最初に「はい」になった所で止まる）と、状態ごとの使い方（型08）",
           "条件": {"状態": st or "指定なし"},
           "この道具がしないこと": "状態を判定しない。問いに、アカウントの数字（run_checks・get_performance など）で上から答えるのは呼ぶ側。"
                             "答えられない問いの扱いは 型06§2-2（確かめていない計測は「当たる」とする など）。",
           "決定木の場所": {"ファイル": doc06["rel"], "節": sec["path"], "行": sec["line"], "確認日": doc06["date"]}}
    if st:
        out["この状態に行き着く問い"] = [q for q in qs if any(s in q["行き先"] for s in st)]
        five = []
        for u in doc06["units"]:
            if u["type"] == "表の行" and "触ってはいけないもの" in _hn(u) and any(x.startswith("最初に") for x in _hn(u)):
                if any(re.match(rf"^{s}(?!\d)", _first(u)) for s in st):
                    five.append({**{k: _clean(v) for k, v in u["cells"].items()}, **_loc(doc06, u)})
        out["状態ごとの打ち手（型06§5）"] = five
        out["問い（全部）"] = [{"問": q["問"], "問い": q["問い"], "行き先": q["行き先"]} for q in qs]
    else:
        out["問い"] = qs
        out["決定木（そのまま）"] = block
    out["判定の基準の節（型06§3）"] = crit
    out["状態ごとの使い方（型08）"] = use
    out["次に引くもの"] = "状態が決まったら kb_kata(state='状N') でその状態の規則を引く。状4 は 型06§4 の順で制約を 1 つに特定してから。"
    if not qs:
        out["注記"] = ["決定木の中に「問 N」の行が見つからなかった。決定木（そのまま）を読む。"]
    return out


# ---------------- 型92・型93: 裁定と未確認 ----------------

def _section_text(doc: dict, s: dict, n: int) -> str:
    return kb._cut("\n".join(doc["lines"][s["line"] - 1:s["end"]]), n)


def rulings_lookup(query: str = "", which: str = "裁定", max_results: int = 10, max_chars: int = 9000) -> dict:
    p = parse_query(query)
    doc = _doc("92" if which == "裁定" else "93")
    max_results = kb._clamp(max_results, 10, kb.MAX_RESULTS)
    max_chars = kb._clamp(max_chars, 9000, kb.MAX_CHARS)
    items: List[dict] = []
    ids = p["rulings"] if which == "裁定" else [f"D{int(n)}" for n in re.findall(r"(?<![A-Za-z])[Dd](\d+)(?!\d)", _nfkc(p["rest"]))]
    words = p["words"] if which == "裁定" else [w for w in p["words"] if not re.fullmatch(r"d\d+", w)]
    for rid in ids:
        sec = next((s for s in doc["sections"] if re.match(rf"^{re.escape(rid)}(?!\d)", _nfkc(s["title"]))), None)
        if sec:
            items.append({"形": "節", "番号": rid, "見出し": sec["title"], "本文": _section_text(doc, sec, 3000),
                          "ファイル": doc["rel"], "節": sec["path"], "行": sec["line"], "確認日": doc["date"]})
            continue
        row = next((u for u in doc["units"] if u["type"] == "表の行" and _nfkc(_first(u)) == rid), None)
        if row:
            items.append({"形": "表の行", "番号": rid, "行の中身": {k: kb._cut(_clean(v), 800) for k, v in row["cells"].items()},
                          **_loc(doc, row)})
        else:
            items.append({"番号": rid, "見つからない": f"{doc['rel']} に {rid} の節も行も無い"})
    if words:
        hits = sorted(((-(kb._score(u, words)), u["line"], u) for u in doc["units"] if kb._score(u, words)), key=lambda x: x[:2])
        for _, _, u in hits[:max_results]:
            it: Dict[str, object] = {"形": u["type"], **_loc(doc, u)}
            if u["type"] == "表の行":
                it["行の中身"] = {k: kb._cut(_clean(v), 400) for k, v in u["cells"].items()}
            elif u["type"] == "見出し":
                sec = next((s for s in doc["sections"] if s["line"] == u["line"]), None)
                it["見出し"] = u["text"]
                if sec:
                    it["本文"] = _section_text(doc, sec, 1500)
            else:
                it["抜粋"] = kb._snippet(u["text"], words, 300)
            it["印"] = kb._mark_of(u)
            items.append(it)
        total = len(hits)
    else:
        total = len(items)
    if not ids and not words:
        items = [{"見出し": s["title"], "行": s["line"], "読むには": f"kb_read(file='{doc['ref']}', section='{s['title']}')"}
                 for s in doc["sections"] if s["level"] >= 2]
        total = len(items)
    shown, cut = kb._budget(items, max_chars)
    out = {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
           "単位": ("型92（付録C 食い違いと裁定）" if which == "裁定" else "型93（付録D 未確認の点）") + "の節・表の行・本文",
           "条件": {"番号": ids or "指定なし", "語（すべて含む）": words},
           "件数": {"当たり": total, "表示": len(shown)}, "行": shown}
    notes = []
    if which == "未確認":
        notes.append("ここに載っている点は、答えるときに「未確認」と言う。確かめ方の列があれば添える。")
    if cut:
        notes.append(f"文字数の上限（{max_chars} 字）で打ち切った。")
    if words and not total:
        notes.append("当たりが無い。語を減らすか、kb_search で全体を探す。")
    if notes:
        out["注記"] = notes
    return out


# ---------------- 入口 ----------------

def _kind(kind: str) -> str:
    if not kind:
        return ""
    k = _KIND_ALIAS.get(kb.norm(kind))
    if not k:
        raise kb.KBError(f"kind は {' / '.join(KATA_KINDS)} のどれか（空なら query の番号から決める）")
    return k


def guide() -> dict:
    doc = _doc("08")
    return {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）",
            "単位": "kb_kata の引き方と、型08 の分類",
            "引き方": {"規則": "query に 規換-15（番号）／規換（分類）／語。state='状4' で使う状態で絞る",
                     "しきい値": "query に 閾08 か語（kind='しきい値'）", "式": "query に 式 4 か語（kind='式'）",
                     "状態": "kind='状態'（決定木の問い）。state を付けるとその状態の使い方",
                     "裁定": "kind='裁定' で 92 から（裁定 3 か語）", "未確認": "kind='未確認' で 93 から（D2 か語）"},
            "分類（型08）": categories(doc),
            "状態ごとの使い方（型08）": state_rows(doc)}


def kata(query: str = "", kind: str = "", state: str = "", max_results: int = DEFAULT_RESULTS, max_chars: int = 9000) -> dict:
    k = _kind(kind)
    p = parse_query(query)
    if not k:
        if p["rule_ids"] or p["cats"] or p["states"] or state:
            k = "規則"
        elif p["thr"]:
            k = "しきい値"
        elif p["formulas"]:
            k = "式"
        elif p["rulings"]:
            k = "裁定"
        elif p["words"]:
            k = "規則"
        else:
            return guide()
    if k == "規則":
        return rules_lookup(query, state, max_results, max_chars)
    if k == "しきい値":
        return thresholds_lookup(query, max_results, max_chars)
    if k == "式":
        return formulas_lookup(query, max_results, max_chars)
    if k == "状態":
        return state_guide(state or " ".join(p["states"]))
    return rulings_lookup(query, k, max_results, max_chars)
