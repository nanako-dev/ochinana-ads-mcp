"""文章の数字の突き合わせ。書き上がったレポートの数字が、数字の束（report.py）の中に全部あるかを機械で確かめる。

考え方は LP の check_markers.py と同じ: 人や AI が打ち直した数字を信じず、元の出力と照らす。
- 書かれた桁で丸めて一致すれば「一致」（12,345 円を「約 1.2 万円」と書いたら、千の位で丸めて一致）
- 束に無い数字は「束に無い」。AI の暗算・写し間違い・別の資料から持ち込んだ数字の疑い
- 直前に書いた指標（「クリック単価（+5.0%）」）の項目に無ければ「指標が違う疑い」
- 符号まで書いてあって、束の値と向きが逆なら「符号が違う」
- 日付・時刻・見出しの番号・ID は数字として見ない（「見ていない」に出す）
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

from .config import out_dir

BUNDLE_DIR = "report_bundles"

UNIT_ALIASES = {"円": "円", "%": "%", "％": "%", "pt": "pt", "ポイント": "pt", "件": "件", "回": "回", "倍": "倍",
                "日": "日", "日間": "日"}
UNIT_RE = "|".join(sorted(map(re.escape, UNIT_ALIASES), key=len, reverse=True))

NUM_RE = re.compile(
    r"(?P<sign>[+\-−－▲△＋])?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<mult>万|億)?\s?(?P<unit>" + UNIT_RE + r")?"
)
SKIP_PATTERNS = [
    (r"【書く:[^】]*】", "書く欄の指示（骨組みの中）"),
    (r"\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?", "日付"),
    (r"\d{1,2}月\d{1,2}日", "日付"),
    (r"\d{1,2}/\d{1,2}(?!\d)", "日付"),
    (r"\d{1,2}月(?!\d)", "日付"),
    (r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", "時刻"),
    (r"[A-Za-z_]+\s?=\s?\d+(?:\.\d+)?", "設定の値（引数など）"),
    (r"\d+\s?日(?:の予算|あたり|の平均|の上限|目|で|に|ごと)", "数え（束の外）"),
    (r"\d{1,2}日（[月火水木金土日]）", "日付"),
    (r"\d{1,2}日\([月火水木金土日]\)", "日付"),
    (r"(?m)^\s*#{1,6}\s*\d+(?:\.\d+)*", "見出しの番号"),
    (r"(?m)^\s*\d+[.)．、]\s", "箇条の番号"),
    (r"[A-Za-z_]+[\-_]?\d+", "記号つきの番号（ID・版など）"),
    (r"\d{7,}", "長い番号（ID）"),
    (r"第\d+", "順番"),
    (r"\d+日(?:平均|移動平均|間の平均)", "数え（束の外）"),
    (r"\d+\s?(?:位|つ|個|本|語|人|項目|週|か月|ヶ月|カ月|年|時間|分|秒|段|層|行|節|章|割|案|点)", "数え（束の外）"),
]

TOL = 1e-9

# 数字の直前に書かれた指標の名前（長いものから当てる）→ 束の項目名に含まれるはずの名前
METRIC_WORDS = [
    ("コンバージョン単価", "コンバージョン単価"), ("コンバージョン率", "コンバージョン率"), ("コンバージョン値", "コンバージョン値"),
    ("獲得単価", "コンバージョン単価"), ("クリック単価", "クリック単価"), ("クリック率", "クリック率"), ("クリック数", "クリック数"),
    ("表示回数", "表示回数"), ("1件あたりの値", "1件あたりの値"), ("CPA", "コンバージョン単価"), ("CPC", "クリック単価"),
    ("CTR", "クリック率"), ("CVR", "コンバージョン率"), ("ROAS", "ROAS"), ("CV", "コンバージョン"),
    ("コンバージョン", "コンバージョン"), ("成果", "コンバージョン"), ("費用", "費用"), ("コスト", "費用"), ("消化", "費用"),
]
# 「表示」「クリック」だけの語は「表示の機会」「クリックされた」のように別の名詞の一部になりやすいので、指標の名前として使わない
WINDOW = 30


def _metric_before(text: str, pos: int) -> Optional[tuple]:
    """数字の直前（同じ文・同じ行・同じ表のます）に書かれた、いちばん近い指標の名前。"""
    lo = max(0, pos - WINDOW)
    seg = text[lo:pos]
    cut = max(seg.rfind(c) for c in "。\n|")
    nums = list(re.finditer(r"\d", seg))
    if nums:  # 間に別の数字があれば、その前の指標の名前はその数字のもの（「CPA 6,295 円は、2.0 件」の 2.0 は CPA ではない）
        cut = max(cut, nums[-1].start())
    if cut >= 0:
        seg = seg[cut + 1:]
    best = None
    taken = []
    for word, canon in METRIC_WORDS:
        for m in re.finditer(re.escape(word), seg):
            if any(a <= m.start() < b for a, b in taken):
                continue  # 長い名前の一部（「コンバージョン単価」の中の「コンバージョン」）は数えない
            taken.append((m.start(), m.end()))
            if best is None or m.end() > best[0]:
                best = (m.end(), word, canon)
    return (best[1], best[2]) if best else None


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("−", "-")


def bundle_path(bundle_id: str) -> Path:
    if not re.fullmatch(r"R\d{8}-[0-9a-f]{8}", bundle_id or ""):
        raise ValueError(f"束ID の形が違う（report_numbers の返した「束ID」をそのまま渡す。受け取った値: {bundle_id!r}）")
    return out_dir() / BUNDLE_DIR / f"{bundle_id}.json"


def save_bundle(bundle: dict, numbers: List[dict], rows: Optional[List[dict]] = None) -> Path:
    """束と数字の一覧を保存する。rows（共通の形の日別の行）も残すと、スプレッドシートの元のデータに使える。"""
    p = bundle_path(bundle["束ID"])
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"束": bundle, "数字": numbers}
    if rows is not None:
        data["元の行"] = rows
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_file(bundle_id: str) -> dict:
    p = bundle_path(bundle_id)
    if not p.exists():
        raise ValueError(f"束 {bundle_id} が見つからない（{p}）。report_numbers を先に呼ぶ")
    return json.loads(p.read_text(encoding="utf-8"))


def load_numbers(bundle_id: str) -> List[dict]:
    p = bundle_path(bundle_id)
    if not p.exists():
        raise ValueError(f"束 {bundle_id} が見つからない（{p}）。report_numbers を先に呼ぶ")
    return json.loads(p.read_text(encoding="utf-8"))["数字"]


def _spans(text: str) -> List[tuple]:
    out = []
    for pat, why in SKIP_PATTERNS:
        for m in re.finditer(pat, text):
            out.append((m.start(), m.end(), why))
    return out


def _precision(num: str, mult: Optional[str]) -> float:
    dec = len(num.split(".")[1]) if "." in num else 0
    m = {"万": 10_000, "億": 100_000_000}.get(mult or "", 1)
    return 10 ** (-dec) * m


def _compatible(text_unit: Optional[str], bundle_unit: str) -> bool:
    if text_unit is None:
        return True
    if text_unit == "pt":
        return bundle_unit in ("pt",)
    if text_unit == "%":
        return bundle_unit in ("%", "pt")  # 「0.3% 下がった」と pt を % で書く人も多い。一致させて、注意を付ける
    return text_unit == bundle_unit


def check(text: str, numbers: List[dict], allow: Optional[List] = None) -> dict:
    t = _norm(text)
    skips = _spans(t)
    allow_vals = []
    for a in allow or []:
        try:
            allow_vals.append(float(str(a).replace(",", "")))
        except ValueError:
            raise ValueError(f"allow には数字を渡す（受け取った値: {a!r}）") from None
    found, skipped = [], []
    for m in NUM_RE.finditer(t):
        s, e = m.start("num"), m.end()
        span = next(((a, b, w) for a, b, w in skips if a <= s < b), None)
        ctx = t[max(0, m.start() - 14):min(len(t), e + 10)].replace("\n", " ")
        if span:
            item = {"文字列": t[span[0]:span[1]].strip(), "理由": span[2]}
            if item not in skipped:
                skipped.append(item)
            continue
        num, mult = m.group("num"), m.group("mult")
        unit = UNIT_ALIASES.get(m.group("unit")) if m.group("unit") else None
        v = float(num.replace(",", "")) * ({"万": 10_000, "億": 100_000_000}.get(mult or "", 1))
        prec = _precision(num, mult)
        sign = m.group("sign")
        neg = sign in ("-", "▲", "△") if sign else None
        hits = []
        for n in numbers:
            if not _compatible(unit, n["単位"]):
                continue
            bv = float(n["値"])
            if abs(abs(bv) - v) <= prec / 2 + TOL:
                hits.append(n)
        row = {"文字列": m.group(0).strip(), "文脈": ctx, "値": v, "単位": unit or "（なし）"}
        if any(abs(v - a) <= TOL for a in allow_vals) and not hits:
            row["結果"] = "許可（allow）"
        elif not hits:
            row["結果"] = "束に無い"
        else:
            if neg is not None and v != 0:
                same = [h for h in hits if (float(h["値"]) < 0) == neg]
                if not same:
                    row["結果"] = "符号が違う"
                    row["束の値"] = [f"{h['項目']}: {h['値']}{h['単位']}" for h in hits[:3]]
                    found.append(row)
                    continue
                hits = same
            near = _metric_before(t, m.start()) if unit not in ("日",) else None
            if near:
                named = [h for h in hits if near[1] in h["項目"] or near[0] in h["項目"]]
                if not named:
                    row["結果"] = "指標が違う疑い"
                    row["直前の指標"] = near[0]
                    row["束の値"] = [f"{h['項目']}: {h['値']}{h['単位']}" for h in hits[:3]]
                    found.append(row)
                    continue
                hits = named
            row["結果"] = "一致"
            row["一致した項目"] = [f"{h['項目']}: {h['値']}{h['単位']}" for h in hits[:3]]
            if len(hits) > 3:
                row["一致した項目"].append(f"ほか {len(hits) - 3} 件")
            if unit == "%" and all(h["単位"] == "pt" for h in hits):
                row["注意"] = "率の差は pt（ポイント）。% と書くと増減率と取り違えやすい"
            if unit is None:
                row["注意"] = "単位が書かれていないので、どの数字か決めきれない（単位を付けると確かになる）"
        found.append(row)
    bad = [r for r in found if r["結果"] in ("束に無い", "符号が違う", "指標が違う疑い")]
    return {
        "結果": "全部一致" if not bad else f"{len(bad)} 件が束と合わない",
        "件数": {"見た数字": len(found), "一致": sum(r["結果"] == "一致" for r in found),
               "束に無い": sum(r["結果"] == "束に無い" for r in found),
               "符号が違う": sum(r["結果"] == "符号が違う" for r in found),
               "指標が違う疑い": sum(r["結果"] == "指標が違う疑い" for r in found),
               "許可（allow）": sum(r["結果"] == "許可（allow）" for r in found), "見ていない": len(skipped)},
        "直すもの": bad,
        "一致": [r for r in found if r["結果"] == "一致"],
        "見ていない": skipped,
        "読み方": ["「束に無い」は、束の数字を写し間違えたか、暗算したか、別の資料から持ち込んだ数字。束から引き直すか、"
                "出どころがはっきりした数字なら allow に入れる（例: 判断の記録の目標値）。",
                "「指標が違う疑い」は、数字は束にあるが、直前に書いた指標（例: クリック単価）の項目には無いもの。"
                "別の指標の数字を取り違えた疑い。直前の指標の名前は同じ文の 30 字以内から探す。",
                "書いた桁で丸めて照らすので、「約 1.2 万円」は 11,500〜12,499 円のどれかと一致する。桁を粗く書くほど、別の数字とも一致しやすい。",
                "日付・時刻・見出しや箇条の番号・ID・「3 つ」「5 本」などの数えは見ていない（「見ていない」の欄）。"],
    }


def _unit_from_key(key: str) -> str:
    for mark, unit in (("円", "円"), ("pt", "pt"), ("%", "%"), ("件", "件"), ("回", "回"), ("日数", "日"), ("日", "日")):
        if mark in key:
            return unit
    if any(w in key for w in ("費用", "単価", "予算")):
        return "円"
    if any(w in key for w in ("表示", "クリック数")):
        return "回"
    if "コンバージョン" in key or "成果" in key:
        return "件"
    return ""


def flatten(obj, label: str, unit: str = "") -> List[dict]:
    """道具の返答の中の数字を、突き合わせ用の行に平らにする（キーの名前から単位を推す）。"""
    out: List[dict] = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.append({"項目": label, "値": obj, "単位": unit})
    elif isinstance(obj, dict):
        name = obj.get("指標") or obj.get("対象") or obj.get("点検")
        for k, v in obj.items():
            if k in ("ID", "id", "キャンペーン ID", "版"):
                continue
            u = _unit_from_key(str(k)) or (_unit_from_key(str(name)) if name else "") or unit
            out.extend(flatten(v, f"{label}・{name + '・' if name and k not in ('指標', '対象') else ''}{k}", u))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.extend(flatten(v, f"{label}[{i + 1}]", unit))
    return out


def add_numbers(bundle_id: str, items: List[dict]) -> int:
    """束のファイルに、ほかの道具の数字を足す（外れ・変更・判断の結果など）。足した数を返す。"""
    p = bundle_path(bundle_id)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["数字"].extend(items)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(items)
