"""理解度テスト（知識ベースの quiz.jsonl）を引く。読むだけで、書き換えない。

- 1 行 1 問の JSON。呼ばれるたびに更新時刻と大きさを見て、変わっていれば読み直す（中身をコードに写さない）。
- 問いを取り出すときは、既定で正解を返さない（解かせる前に答えが見えないように）。答え合わせは id を指定する。
- 主張の点検: 文を渡すと、近い「よくある誤答」を探し、正解と根拠を返す（kb_check_claim の先頭にも出る）。
"""
from __future__ import annotations

import ast
import json
import random
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import kb

QUIZ_FILE = "quiz.jsonl"
QUIZ_KEYS = ("id", "分野", "難しさ", "型", "問い", "正解", "よくある誤答", "なぜ誤答するか", "根拠", "印", "採点の要点")
QUESTION_KEYS = ("id", "分野", "型", "難しさ", "問い")  # 既定で返すのはこれだけ
ANSWER_KEYS = ("正解", "よくある誤答", "なぜ誤答するか", "根拠", "印", "採点の要点")
_cache: Dict[str, Tuple[int, int, dict]] = {}


def _listish(v: Any) -> Any:
    """「['01 §9', '01 §11']」のように文字で入っている一覧を、一覧に直す（読めなければそのまま）。"""
    if isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]"):
        try:
            got = ast.literal_eval(v.strip())
            if isinstance(got, list):
                return got
        except (ValueError, SyntaxError):
            pass
    return v


def quiz_rows(folder: Optional[Path] = None) -> Optional[dict]:
    """quiz.jsonl を読む。無ければ None。{"rows": [...], "bad_lines": [...], "path": ...}"""
    try:
        base = folder or kb.kb_dir()
    except kb.KBError:
        return None
    p = base / QUIZ_FILE
    if not p.is_file():
        return None
    st = p.stat()
    hit = _cache.get(str(p))
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    rows, bad = [], []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            bad.append(i)
            continue
        if not isinstance(r, dict) or not r.get("id"):
            bad.append(i)
            continue
        r = {k: _listish(v) for k, v in r.items()}
        r["_行"] = i
        r["_n問い"] = kb.norm(f"{r.get('問い', '')} {r.get('分野', '')}")
        r["_n誤答"] = kb.norm(r.get("よくある誤答", ""))
        rows.append(r)
    out = {"rows": rows, "bad_lines": bad, "path": str(p)}
    _cache[str(p)] = (st.st_mtime_ns, st.st_size, out)
    return out


def _must() -> dict:
    q = quiz_rows()
    if q is None:
        raise kb.KBError(f"理解度テスト（{QUIZ_FILE}）が知識ベースのフォルダに無い")
    return q


def _head(unit: str) -> dict:
    return {"対象アカウント": "なし（知識ベース）", "期間": "指定なし（いまのファイル）", "単位": unit}


def _where(r: dict) -> dict:
    return {"ファイル": QUIZ_FILE, "行": r["_行"]}


def _ids(id: str) -> List[str]:
    t = unicodedata.normalize("NFKC", str(id or ""))
    return [x.strip().upper() for x in t.replace("、", ",").replace(" ", ",").split(",") if x.strip()]


def _field_code(r: dict) -> str:
    """id の分野の記号（M-AC-03 の ac、Google の KW-31 の kw）。分野の名前の代わりに記号でも絞れる。"""
    parts = str(r.get("id", "")).split("-")
    return kb.norm(parts[-2]) if len(parts) >= 2 else ""


def questions(query: str = "", field: str = "", difficulty: str = "", type: str = "", mark: str = "",
              count: int = 5, shuffle: bool = False, seed: int = 0, max_chars: int = kb.DEFAULT_CHARS) -> dict:
    q = _must()
    words = kb._words(query)
    if mark and mark not in kb.MARKS:
        raise kb.KBError("印は ✅ / △ / ❓ のどれか")
    f, d, t = kb.norm(field), kb.norm(difficulty), kb.norm(type)
    hits = [r for r in q["rows"]
            if (not f or f in kb.norm(r.get("分野", "")) or f == _field_code(r))
            and (not d or d == kb.norm(r.get("難しさ", "")))
            and (not t or t in kb.norm(r.get("型", "")))
            and (not mark or r.get("印") == mark)
            and all(w in r["_n問い"] for w in words)]
    count = kb._clamp(count, 5, kb.MAX_RESULTS)
    picked = random.Random(seed).sample(hits, min(count, len(hits))) if shuffle else hits[:count]
    items = [{**{k: r.get(k) for k in QUESTION_KEYS}, **_where(r)} for r in picked]
    shown, cut = kb._budget(items, kb._clamp(max_chars, kb.DEFAULT_CHARS, kb.MAX_CHARS))
    out = {**_head("理解度テストの 1 問（問いだけ。正解・誤答・根拠は返さない）"),
           "条件": {"語（問いと分野にすべて含む）": words, "分野": field or "指定なし", "難しさ": difficulty or "指定なし",
                  "型": type or "指定なし", "印": mark or "指定なし", "並び": f"ばらばら（seed={seed}）" if shuffle else "ファイルの順"},
           "件数": {"当たり": len(hits), "表示": len(shown), "全部": len(q["rows"])},
           "行": shown,
           "答え合わせ": "kb_quiz(id='…') で 正解・よくある誤答・なぜ誤答するか・根拠・採点の要点 を返す。"
                     "採点の基準（1 問 4 点）は kb_read(file='93', section='採点の基準')。"}
    notes = []
    if cut:
        notes.append("文字数の上限で打ち切った。")
    if not hits:
        notes.append("当たる問いが無い。分野は部分一致（例 入札、計測、LP）、難しさは 基本／実務／難。")
    if q["bad_lines"]:
        notes.append(f"{QUIZ_FILE} の {len(q['bad_lines'])} 行を読めなかった（行 {q['bad_lines'][:5]}）。")
    if notes:
        out["注記"] = notes
    return out


def answers(id: str, max_chars: int = 12000) -> dict:
    q = _must()
    want = _ids(id)
    if not want:
        raise kb.KBError("答え合わせには id（例 ML-01。カンマ区切りで複数）を渡す")
    by_id = {str(r["id"]).upper(): r for r in q["rows"]}
    missing = [w for w in want if w not in by_id]
    items = [{**{k: by_id[w].get(k) for k in QUIZ_KEYS}, **_where(by_id[w])} for w in want if w in by_id]
    shown, cut = kb._budget(items, kb._clamp(max_chars, 12000, kb.MAX_CHARS))
    out = {**_head("理解度テストの 1 問の答え（正解・よくある誤答・なぜ誤答するか・根拠・印・採点の要点）"),
           "条件": {"id": want}, "件数": {"当たり": len(items), "表示": len(shown)}, "行": shown,
           "印の読み方": "✅ 正解の主な根拠は公式で確認／△ 公式の中で食い違う（両方を答えに入れる）／❓ 公式に記載なし（未確認と答えるのが正解）。"}
    notes = []
    if missing:
        notes.append(f"id が見つからない: {missing}。kb_quiz で問いを取り出して id を確かめる。")
    if cut:
        notes.append("文字数の上限で打ち切った。")
    if notes:
        out["注記"] = notes
    return out


def near_misconceptions(claim: str, max_results: int = 3) -> List[dict]:
    """主張に近い「よくある誤答」。主張の語の 6 割以上（2 語以上）を含む誤答を、近い順に返す。無ければ空。"""
    q = quiz_rows()
    if not q or not str(claim or "").strip():
        return []
    terms, _ = kb.claim_terms(str(claim))
    nterms = [kb.norm(t) for t in terms]
    if not nterms:
        return []
    need = min(2, len(nterms))
    scored = []
    for r in q["rows"]:
        got = [t for t, n in zip(terms, nterms) if n in r["_n誤答"]]
        if len(got) < need or len(got) / len(nterms) < 0.6:
            continue
        scored.append((-len(got) / len(nterms), -len(got), len(r["_n誤答"]), r["_行"], r, got))
    scored.sort(key=lambda x: x[:4])
    out = []
    for *_, r, got in scored[:max(1, max_results)]:
        out.append({"id": r["id"], "近さ": f"主張の語 {len(got)}/{len(nterms)} がこの誤答にある", "合った語": got,
                    "よくある誤答": r.get("よくある誤答"), "正解": kb._cut(r.get("正解", ""), 700),
                    "なぜ誤答するか": kb._cut(r.get("なぜ誤答するか", ""), 300),
                    "根拠": r.get("根拠"), "印": r.get("印"), "問い": kb._cut(r.get("問い", ""), 200), **_where(r)})
    return out


def misconceptions(claim: str, max_results: int = 5, max_chars: int = kb.DEFAULT_CHARS) -> dict:
    if not str(claim or "").strip():
        raise kb.KBError("誤答に近いかを見たい文（主張）か語を query に渡す")
    _must()
    near = near_misconceptions(claim, kb._clamp(max_results, 5, kb.MAX_RESULTS))
    shown, cut = kb._budget(near, kb._clamp(max_chars, kb.DEFAULT_CHARS, kb.MAX_CHARS))
    out = {**_head("理解度テストの「よくある誤答」1 つ（近い順。正解と根拠つき）"),
           "条件": {"主張": claim, "使った語": kb.claim_terms(claim)[0]},
           "件数": {"当たり": len(near), "表示": len(shown)}, "行": shown,
           "この道具がしないこと": "主張が誤りだとは判定しない。近い誤答があるときは、正解と根拠を読んで主張と見比べる。"}
    if not near:
        out["注記"] = ["近い誤答は無かった（主張の語の 6 割以上を含む誤答が無い）。kb_check_claim で本文も照らす。"]
    elif cut:
        out["注記"] = ["文字数の上限で打ち切った。"]
    return out


def quiz(query: str = "", field: str = "", difficulty: str = "", type: str = "", mark: str = "", id: str = "",
         mode: str = "", count: int = 5, shuffle: bool = False, seed: int = 0, max_chars: int = kb.DEFAULT_CHARS) -> dict:
    m = kb.norm(mode)
    if m in ("答え", "答え合わせ", "answer") or (id and not m):
        return answers(id, max_chars=max(max_chars, 12000))
    if m in ("誤答", "よくある誤答", "主張"):
        return misconceptions(query, count, max_chars)
    if m and m not in ("問い", "問題", "question"):
        raise kb.KBError("mode は 問い（既定）／答え（id を渡す）／誤答（query に主張を渡す）のどれか")
    return questions(query, field, difficulty, type, mark, count, shuffle, seed, max_chars)
