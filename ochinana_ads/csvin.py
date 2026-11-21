"""媒体の管理画面から書き出した CSV を読み、レポートの共通の形の行（report.py の冒頭）に直す。API は呼ばない。

対象: Google 広告・Meta・LINEヤフー・ChatGPT 広告・そのほか（列の名前が日本語か英語で分かるもの）。
- 文字コード: UTF-8（BOM あり・なし）、UTF-16（BOM あり。Google 広告の「Excel 用 CSV」）、Shift_JIS（cp932）
- 区切り: カンマかタブ
- 見出しの行: 書き出しの先頭に表題や期間の行が 1〜2 行あることが多い。先頭 30 行から、列の名前が 3 つ以上当たる最初の行を見出しとみなす
- 数字: 「¥」「,」「%」「円」は外す。「--」「-」と空は 0
- 飛ばす行: 空の行、合計の行（合計・総計・Total・全体）、日付の無い行
- 日別の行が要る。日付の列が無ければ断る（管理画面で「日」で分けて書き出し直す）
列の名前の候補は COLUMN_ALIASES。当たらないときは columns で {"cost": "ご利用金額"} のように指定する。
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FIELDS_JA = {
    "date": "日付", "campaign": "キャンペーン名", "campaign_id": "キャンペーン ID", "media": "媒体",
    "impressions": "表示回数", "clicks": "クリック数", "cost": "費用", "conversions": "コンバージョン",
    "value": "コンバージョン値", "currency": "通貨",
}
REQUIRED = ("date", "campaign", "impressions", "clicks", "cost", "conversions")

# 列の名前の候補。前にあるほど優先する（同じ欄に当たる列が 2 つあれば、前の名前の列を使う）
COLUMN_ALIASES: Dict[str, List[str]] = {
    "date": ["日", "日付", "day", "date", "レポート開始日", "reporting starts", "期間", "集計日"],
    "campaign": ["キャンペーン", "キャンペーン名", "campaign", "campaign name", "キャンペーンの名前"],
    "campaign_id": ["キャンペーン id", "キャンペーンid", "campaign id", "キャンペーン番号"],
    "media": ["媒体", "メディア", "media", "platform", "プラットフォーム"],
    "impressions": ["表示回数", "インプレッション数", "インプレッション", "impressions", "impr.", "imp", "表示"],
    "clicks": ["クリック数", "クリック", "clicks", "リンクのクリック", "link clicks", "クリック(すべて)", "clicks (all)"],
    "cost": ["費用", "コスト", "cost", "消化金額", "amount spent", "金額", "ご利用金額", "spend", "広告費"],
    "conversions": ["コンバージョン", "コンバージョン数", "conversions", "conv.", "cv", "cv数", "結果", "results",
                    "購入", "purchases"],
    "value": ["コンバージョン値", "コンバージョンの価値", "conv. value", "conversion value", "conversions value",
              "購入の価値", "購入のコンバージョン値", "purchases conversion value", "コンバージョン値(円)"],
    "currency": ["通貨コード", "通貨", "currency", "currency code"],
}
# 当たり方に注意が要る列（読み方の注意に出す）
ALIAS_NOTES = {
    "結果": "Meta の「結果」は、キャンペーンの目的ごとに成果の種類が違う（購入・リード・リンクのクリックなど）。中身を管理画面で確かめる。",
    "results": "Meta の「Results」は、キャンペーンの目的ごとに成果の種類が違う。中身を管理画面で確かめる。",
    "クリック(すべて)": "「クリック（すべて）」は、リンク以外（いいね・プロフィール・画像の拡大など）も数える。リンクのクリックと比べると多く出る。",
    "clicks (all)": "「Clicks (all)」は、リンク以外のクリックも数える。リンクのクリックと比べると多く出る。",
    "期間": "「期間」の列を日付として読んだ。1 行が 1 日でない（期間の合計の）書き出しなら、日別で書き出し直す。",
}
TOTAL_WORDS = ("合計", "総計", "total", "全体", "summary", "すべてのキャンペーン")
MEDIA_NAMES = [
    (("google", "グーグル"), "Google 広告"),
    (("meta", "facebook", "instagram", "フェイスブック", "インスタ"), "Meta"),
    (("line", "yahoo", "ヤフー", "lineヤフー"), "LINEヤフー"),
    (("chatgpt", "openai"), "ChatGPT 広告"),
]
FIELD_KEYS = {**{k: k for k in FIELDS_JA}, **{v: k for k, v in FIELDS_JA.items()},
              "キャンペーン": "campaign", "name": "campaign", "id": "campaign_id", "費用（円）": "cost",
              "コンバージョン値（円）": "value"}
HEADER_SCAN = 30
EN_MONTHS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def norm(s: str) -> str:
    """列の名前を比べる形に直す（全角を半角に、小文字に、前後の空白を外し、空白は 1 つに）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    return re.sub(r"\s+", " ", s)


def _strip_paren(s: str) -> str:
    """末尾のかっこ（「(円)」「(JPY)」など）を外す。"""
    return re.sub(r"\s*\([^()]*\)\s*$", "", s).strip()


ALIAS_INDEX: Dict[str, Tuple[str, int]] = {}
for _f, _names in COLUMN_ALIASES.items():
    for _i, _n in enumerate(_names):
        ALIAS_INDEX.setdefault(norm(_n), (_f, _i))


def match_header(cell: str) -> Optional[Tuple[str, int]]:
    """見出し 1 ますが、どの欄の何番目の候補に当たるか。"""
    n = norm(cell)
    if not n:
        return None
    if n in ALIAS_INDEX:
        return ALIAS_INDEX[n]
    s = _strip_paren(n)
    if s != n and s in ALIAS_INDEX:
        f, i = ALIAS_INDEX[s]
        return f, i + 0.5  # かっこを外して当たったものは、そのまま当たったものより後
    return None


def normalize_media(media: str) -> str:
    m = norm(media).replace(" ", "")
    if not m:
        return ""
    for keys, name in MEDIA_NAMES:
        if any(k in m for k in keys):
            return name
    return str(media).strip()


# ---------------- ファイルを読む ----------------

def decode(raw: bytes) -> Tuple[str, str]:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "UTF-16"
    for enc, label in (("utf-8-sig", "UTF-8"), ("cp932", "Shift_JIS（cp932）")):
        try:
            return raw.decode(enc), label
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを読めなかった（UTF-8 か Shift_JIS か UTF-16 で書き出し直す）")


def _delimiter(text: str, suffix: str) -> str:
    sample = "\n".join(text.splitlines()[:HEADER_SCAN])
    if suffix == ".tsv":
        return "\t"
    return "\t" if sample.count("\t") > sample.count(",") else ","


def _find_header(table: List[List[str]], override: Dict[str, str]) -> int:
    want = {norm(h) for h in override.values() if h}
    best = None
    for i, cells in enumerate(table[:HEADER_SCAN]):
        normed = {norm(c) for c in cells}
        if want and not want <= normed:
            continue
        fields = {m[0] for c in cells if (m := match_header(c))}
        hits = len(fields) + len(want)
        if hits >= 3:
            return i
        if want and want <= normed:
            best = i if best is None else best
    if best is not None:
        return best
    seen = [" / ".join(c for c in cells if c.strip()) for cells in table[:5] if any(c.strip() for c in cells)]
    msg = "見出しの行が見つからない（先頭 30 行に、日付・キャンペーン・表示回数・クリック数・費用などの列の名前が 3 つ以上ある行が無い）。"
    if want:
        msg = f"columns で指定した列の名前 {sorted(want)} がそろう行が、先頭 30 行に無い。"
    raise ValueError(msg + f" 先頭の行: {seen}。columns で {{\"cost\": \"列の名前\"}} のように指定できる")


def _map_columns(header: List[str], override: Dict[str, str]) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
    cols: Dict[str, int] = {}
    rank: Dict[str, float] = {}
    others: Dict[str, List[str]] = {}
    normed = [norm(h) for h in header]
    for f, h in override.items():
        if not h:
            continue
        cols[f] = normed.index(norm(h))
        rank[f] = -1
    for j, h in enumerate(header):
        m = match_header(h)
        if not m:
            continue
        f, i = m
        if f in override:
            if cols.get(f) != j:
                others.setdefault(f, []).append(h)
            continue
        if f not in cols or i < rank[f]:
            if f in cols:
                others.setdefault(f, []).append(header[cols[f]])
            cols[f], rank[f] = j, i
        else:
            others.setdefault(f, []).append(h)
    return cols, others


def _override(columns: Optional[dict]) -> Dict[str, str]:
    if not columns:
        return {}
    if not isinstance(columns, dict):
        raise ValueError('columns は {"欄": "CSV の列の名前"} の形で渡す（例: {"cost": "ご利用金額"}）')
    out = {}
    for k, v in columns.items():
        f = FIELD_KEYS.get(str(k).strip()) or FIELD_KEYS.get(norm(k))
        if not f:
            raise ValueError(f"columns の欄の名前が分からない: {k!r}。使えるのは {', '.join(FIELDS_JA)}")
        out[f] = str(v or "").strip()
    return out


def parse_date(v: str) -> Optional[date]:
    s = unicodedata.normalize("NFKC", str(v or "")).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    s2 = re.sub(r"^[A-Za-z]{3},\s*", "", s)  # 先頭の曜日（Mon, など）
    for f in EN_MONTHS:
        try:
            return datetime.strptime(s2, f).date()
        except ValueError:
            continue
    return None


def parse_num(v: str, where: str) -> float:
    s = unicodedata.normalize("NFKC", str(v or "")).strip()
    s = s.replace("¥", "").replace("\\", "").replace(",", "").replace("%", "").replace("円", "")
    s = re.sub(r"(?i)\s*jpy\s*", "", s).strip()
    if s in ("", "-", "--", "—", "n/a", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{where} の値を数字として読めない: {v!r}") from None


def _is_total(cells: List[str]) -> bool:
    first = next((c for c in cells if c.strip()), "")
    return norm(first).startswith(TOTAL_WORDS)


def _guess_media(header: List[str]) -> str:
    n = {norm(h) for h in header} | {_strip_paren(norm(h)) for h in header}
    if n & {"消化金額", "amount spent", "reporting starts", "レポート開始日", "結果", "results"}:
        return "Meta"
    if n & {"表示回数", "impr.", "conv.", "conv. value"} and n & {"費用", "cost"}:
        return "Google 広告"
    return ""


def read(path: str, media: str = "", columns: Optional[dict] = None) -> dict:
    """CSV を読み、共通の形の行と、読み方の報告を返す。"""
    p = Path(str(path or "")).expanduser()
    if p.suffix.lower() not in (".csv", ".tsv"):
        raise ValueError(f"csv_path は .csv か .tsv のファイルを指す（受け取った値: {path!r}）")
    if not p.is_file():
        raise ValueError(f"CSV のファイルが見つからない: {p}")
    text, enc = decode(p.read_bytes())
    delim = _delimiter(text, p.suffix.lower())
    table = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
    ov = _override(columns)
    hi = _find_header(table, ov)
    header = [c.strip() for c in table[hi]]
    cols, others = _map_columns(header, ov)
    seen = [h for h in header if h]
    if "date" not in cols:
        raise ValueError("日別の行が要るが、日付の列が無い（日・日付・Day・Date など）。管理画面で「日」で分けて書き出し直すか、"
                         f'columns で {{"date": "列の名前"}} を指定する。見出しの列: {seen}')
    if "campaign" not in cols and "campaign_id" in cols:
        cols["campaign"] = cols["campaign_id"]
    missing = [FIELDS_JA[f] for f in REQUIRED if f not in cols]
    if missing:
        raise ValueError(f"必要な列が見つからない: {'・'.join(missing)}。見出しの列: {seen}。"
                         'columns で {"cost": "列の名前"} のように指定できる（欄: ' + ", ".join(FIELDS_JA) + "）")

    notes: List[str] = []
    m_given = normalize_media(media)
    m_guess = "" if m_given or "media" in cols else _guess_media(header)
    if m_guess:
        notes.append(f"媒体は列の名前から「{m_guess}」と推定した（推定。違えば media で指定する）。")
    default_media = m_given or m_guess or "媒体の指定なし"
    if not m_given and not m_guess and "media" not in cols:
        notes.append("媒体が分からなかった。media で「Google 広告」「Meta」「LINEヤフー」「ChatGPT 広告」などを指定する。")
    for f, j in cols.items():
        key = norm(header[j])
        for k, msg in ALIAS_NOTES.items():
            if key == norm(k) or _strip_paren(key) == norm(k):
                notes.append(msg)
    hc = norm(header[cols["cost"]])
    cur_in_head = re.search(r"\(([a-z]{3})\)", hc)
    if cur_in_head and cur_in_head.group(1) != "jpy":
        raise ValueError(f"費用の列が円ではない（{header[cols['cost']]}）。円以外の通貨はまだ扱わない")

    skipped = {"空の行": 0, "合計の行": 0, "日付の無い行": 0}
    merged: Dict[Tuple[str, str, str], dict] = {}
    dup = 0
    for n, cells in enumerate(table[hi + 1:], start=hi + 2):
        if not any(c.strip() for c in cells):
            skipped["空の行"] += 1
            continue

        def cell(f):
            j = cols.get(f)
            return cells[j].strip() if j is not None and j < len(cells) else ""

        raw_d = cell("date")
        d = parse_date(raw_d)
        if d is None:
            if _is_total(cells):
                skipped["合計の行"] += 1
                continue
            if raw_d in ("", "-", "--"):
                skipped["日付の無い行"] += 1
                continue
            raise ValueError(f"{n} 行目の日付を読めない: {raw_d!r}（YYYY-MM-DD・YYYY/MM/DD・YYYY年M月D日 などにする）")
        if not cell("campaign") and not cell("campaign_id"):
            skipped["合計の行"] += 1  # 日付はあるがキャンペーンの無い行は、期間の合計の行とみなす（Meta の書き出しなど）
            continue
        cur = cell("currency")
        if cur and norm(cur) not in ("jpy", "円", "日本円"):
            raise ValueError(f"{n} 行目の通貨が円ではない（{cur}）。円以外の通貨はまだ扱わない")
        name = cell("campaign") or cell("campaign_id") or "（名前なし）"
        key = cell("campaign_id") or name
        if key.endswith(".0") and key[:-2].isdigit():
            key = key[:-2]
        med = normalize_media(cell("media")) or default_media
        r = {"date": d.isoformat(), "key": key, "name": name, "media": med}
        for f in ("impressions", "clicks", "cost", "conversions", "value"):
            r[f] = parse_num(cell(f), f"{n} 行目の{FIELDS_JA[f]}（{header[cols[f]]}）") if f in cols else 0.0
        k = (r["date"], key, med)
        if k in merged:
            dup += 1
            for f in ("impressions", "clicks", "cost", "conversions", "value"):
                merged[k][f] += r[f]
        else:
            merged[k] = r
    rows = sorted(merged.values(), key=lambda r: (r["date"], r["name"]))
    for r in rows:
        for f in ("impressions", "clicks"):
            if float(r[f]).is_integer():
                r[f] = int(r[f])
        for f in ("cost", "conversions", "value"):
            r[f] = round(r[f], 6)
    if not rows:
        raise ValueError("見出しの下に、日付のある行が 1 行も無い")
    if dup:
        notes.append(f"同じ日・同じキャンペーンの行が {dup} 行あったので、足し合わせた。広告グループ別などの書き出しなら正しいが、"
                     "コンバージョン アクション別・デバイス別のように表示回数が重なる分け方なら、表示回数とクリック数が膨らむ。")
    if "value" not in cols:
        notes.append("コンバージョン値の列が無いので、値は 0 とした（ROAS は出ない）。")
    mapping = {FIELDS_JA[f]: header[j] for f, j in sorted(cols.items(), key=lambda x: x[1])}
    report = {
        "ファイル": p.name,
        "文字コード": enc,
        "区切り": "タブ" if delim == "\t" else "カンマ",
        "見出しの行": hi + 1,
        "列の対応": mapping,
        "使わなかった同じ種類の列": {FIELDS_JA[f]: v for f, v in others.items()},
        "使わなかった列": [h for j, h in enumerate(header) if h and j not in cols.values()],
        "読んだ行": len(rows),
        "飛ばした行": skipped,
        "日付の範囲": f"{rows[0]['date']}〜{rows[-1]['date']}",
        "媒体": sorted({r["media"] for r in rows}),
        "注意": notes,
    }
    return {"行": rows, "読み方": report}
