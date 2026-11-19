"""商品フィードの点検（Merchant Center の商品データ）。外へは何も送らない（画像や URL を開きに行かない）。

点検するのは、知識ベース 21（ショッピング広告と商品フィード）の 2 章・6-1・10 章に書かれている決まりだけ。
21 に書かれていない決まり（link など）は点検しない。どの決まりがどの行に書いてあるかを、呼ぶたびに引き直して返す。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import kb

# 属性 → (上限の字数, 根拠の行〔21 の表の 1 列目〕, 確かめる語)
LENGTHS = {
    "id": (50, "`id`", "50"),
    "title": (150, "`title`／`structured_title`", "150"),
    "structured_title": (150, "`title`／`structured_title`", "150"),
    "description": (5000, "`description`／`structured_description`", "5,000"),
    "structured_description": (5000, "`description`／`structured_description`", "5,000"),
    "lifestyle_image_link": (2000, "`lifestyle_image_link`", "2,000"),
    "brand": (70, "`brand`", "70"),
    "mpn": (70, "`mpn`", "70"),
    "product_type": (750, "`product_type`", "750"),
    "item_group_id": (50, "`item_group_id`", "50"),
    "sale_price_effective_date": (51, "`sale_price`", "51"),
    "short_title": (150, "`short_title`", "150"),
    "shipping_label": (100, "`shipping_label`", "100"),
    **{f"custom_label_{i}": (100, "`custom_label_0`〜`4`", "100") for i in range(5)},
}
REQUIRED = ("id", "title", "description", "image_link", "price", "availability")
REQ_REF = {"id": "`id`", "title": "`title`／`structured_title`", "description": "`description`／`structured_description`",
           "image_link": "`image_link`", "price": "`price`", "availability": "`availability`"}
TITLE_RULE = "line:全部大文字・記号・HTML"
AVAILABILITY = ("in_stock", "out_of_stock", "preorder", "backorder")
CONDITION = ("new", "refurbished", "used")
PAUSE = ("ads", "all")
KNOWN = set(LENGTHS) | set(REQUIRED) | {
    "additional_image_link", "video_link", "sale_price", "availability_date", "loyalty_program", "gtin", "identifier_exists",
    "google_product_category", "condition", "age_group", "gender", "color", "size", "promotion_id", "excluded_destination",
    "included_destination", "pause", "shipping", "shipping_weight", "product_highlight", "product_detail", "question_and_answer",
    "related_product", "digital_source_type"}
PROMO = re.compile(r"送料無料|送料込|セール|sale|\d+\s*%\s*(off|オフ)|割引|値下げ|最安|激安|\d[\d,]*\s*円|¥\s*\d|今だけ|限定|ポイント\s*\d+\s*倍", re.I)
PRICE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+([A-Z]{3})\s*$")
MAX_ROWS = 5000


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _gtin_ok(code: str) -> bool:
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _price(name: str, v: str, out: List[dict]) -> Optional[float]:
    m = PRICE.match(v)
    if not m:
        out.append(_issue(name, "書式", f"「数値 通貨」の形にする（例 1980 JPY）。受け取った値: {v!r}", "`price`"))
        return None
    num = m.group(1)
    if "." in num and len(num.split(".")[1]) > 2:
        out.append(_issue(name, "書式", "小数は 2 桁まで", "`price`"))
    if float(num) == 0 and name == "price":
        out.append(_issue(name, "値", "0 は不可（契約付き携帯などを除く）", "`price`"))
    return float(num)


def _issue(attr: str, kind: str, text: str, ref: str, level: str = "直す") -> dict:
    return {"属性": attr, "種類": kind, "内容": text, "重さ": level, "根拠の行": ref}


def check_row(row: Dict[str, Any]) -> List[dict]:
    r = {str(k).strip().lower(): _s(v) for k, v in row.items() if k is not None}
    out: List[dict] = []
    for a in REQUIRED:
        if not r.get(a):
            ref = REQ_REF[a]
            if a == "title" and r.get("structured_title"):
                continue
            if a == "description" and r.get("structured_description"):
                continue
            out.append(_issue(a, "必須の欠け", "必須の属性が空", ref))
    for a, (mx, ref, _) in LENGTHS.items():
        v = r.get(a, "")
        if v and len(v) > mx:
            out.append(_issue(a, "字数", f"{len(v):,} 字。上限 {mx:,} 字", ref))
    # 価格と在庫
    if r.get("price"):
        _price("price", r["price"], out)
    if r.get("sale_price"):
        _price("sale_price", r["sale_price"], out)
        if not r.get("price"):
            out.append(_issue("sale_price", "組み合わせ", "sale_price は通常の price と一緒に送る", "`sale_price`"))
    if r.get("sale_price_effective_date"):
        parts = r["sale_price_effective_date"].split("/")
        if len(parts) == 2 and not parts[0].strip() < parts[1].strip():
            out.append(_issue("sale_price_effective_date", "値", "開始＜終了にする（「開始/終了」の形とみなして比べた）", "`sale_price`"))
    av = r.get("availability", "")
    if av and av not in AVAILABILITY:
        out.append(_issue("availability", "値", f"{av!r} は使えない（{' / '.join(AVAILABILITY)}）", "`availability`"))
    if av in ("preorder", "backorder") and not r.get("availability_date"):
        out.append(_issue("availability_date", "必須の欠け", "予約・取り寄せでは必須（ISO 8601、1 年先まで）", "`availability_date`"))
    # 識別子
    gtin = re.sub(r"[\s-]", "", r.get("gtin", ""))
    if gtin:
        if not gtin.isdigit() or len(gtin) > 14:
            out.append(_issue("gtin", "書式", "数字 14 桁まで", "`gtin`"))
        else:
            if len(gtin) in (8, 12, 13, 14) and not _gtin_ok(gtin):
                out.append(_issue("gtin", "値", "GS1 のチェック数字が合わない", "`gtin`"))
            if gtin.startswith(("02", "04", "05", "98", "99")) or (len(gtin) == 13 and gtin.startswith("2")):
                out.append(_issue("gtin", "値", "先頭が GS1 の制限された範囲（02・04・2）かクーポンの範囲（05・98・99）の GTIN は不可", "`gtin`"))
    ident = r.get("identifier_exists", "").lower()
    if ident and ident not in ("yes", "no", "true", "false"):
        out.append(_issue("identifier_exists", "値", "yes か no", "`identifier_exists`"))
    if not gtin and not r.get("mpn") and ident not in ("no", "false"):
        out.append(_issue("mpn", "必須の欠け", "GTIN が無いときは mpn が必須（どれも当てはまらないなら identifier_exists を no）", "`mpn`"))
    cond = r.get("condition", "")
    if cond and cond not in CONDITION:
        out.append(_issue("condition", "値", f"{cond!r} は使えない（{' / '.join(CONDITION)}）", "`condition`"))
    if not r.get("brand") and cond in ("", "new"):
        out.append(_issue("brand", "必須の欠け", "新品は brand が必須（映画・書籍・音楽を除く。商品の分類は見ていない）", "`brand`", "確かめる"))
    # 色
    color = r.get("color", "")
    if color:
        if len(color) > 100:
            out.append(_issue("color", "字数", "計 100 字まで", "`age_group`・`gender`・`color`・`size`"))
        for c in color.split("/"):
            if len(c.strip()) > 40:
                out.append(_issue("color", "字数", f"1 色 40 字まで（{c.strip()[:20]}…）", "`age_group`・`gender`・`color`・`size`"))
        if re.search(r"\d|#[0-9a-f]{3,6}", color, re.I):
            out.append(_issue("color", "値", "数字や色コードは不可", "`age_group`・`gender`・`color`・`size`"))
    # 画像の追加
    extra = [x for x in re.split(r"[,\s]+", r.get("additional_image_link", "")) if x]
    if len(extra) > 10:
        out.append(_issue("additional_image_link", "数", f"{len(extra)} 枚。10 枚まで", "`additional_image_link`"))
    for x in extra:
        if len(x) > 2000:
            out.append(_issue("additional_image_link", "字数", "1 本 2,000 字まで", "`additional_image_link`"))
    # 販促・配送ほか
    promos = [x for x in re.split(r"[,\s]+", r.get("promotion_id", "")) if x]
    if len(promos) > 10 or any(len(x) > 50 for x in promos):
        out.append(_issue("promotion_id", "数・字数", "1 商品 10 個まで、各 50 字まで", "`promotion_id`"))
    if r.get("pause") and r["pause"] not in PAUSE:
        out.append(_issue("pause", "値", "ads か all", "`pause`"))
    hl = [x for x in re.split(r"\n|\|", r.get("product_highlight", "")) if x.strip()]
    if hl and (not 2 <= len(hl) <= 100 or any(len(x) > 150 for x in hl)):
        out.append(_issue("product_highlight", "数・字数", "2〜100 個、各 150 字まで（区切りは改行か | とみなした）", "`product_highlight`"))
    # タイトルの書き方（6-1）
    title = r.get("title", "")
    if title:
        if re.search(r"<[^>]+>", title):
            out.append(_issue("title", "書き方", "HTML は不可", TITLE_RULE))
        letters = re.sub(r"[^A-Za-z]", "", title)
        if len(letters) >= 5 and letters.isupper():
            out.append(_issue("title", "書き方", "全部大文字は不可", TITLE_RULE))
        hit = PROMO.search(title)
        if hit:
            out.append(_issue("title", "書き方", f"販促の文言に見える語「{hit.group(0)}」（価格・セール・送料などは入れない。語の判定は機械の目安）",
                              "14", "確かめる"))
        if len(title) > 70:
            out.append(_issue("title", "目安", f"{len(title)} 字。目に入るのは先頭の約 70 字なので、大事な情報を先頭に", "14", "確かめる"))
    return out


def _cites(issues: List[dict]) -> List[dict]:
    seen, out = [], []
    for i in issues:
        ref = i["根拠の行"]
        if ref in seen:
            continue
        seen.append(ref)
        out.append(kb.cite_line("21", ref[5:]) if ref.startswith("line:") else kb.cite("21", ref))
    return out


def _read_csv(path: str) -> List[Dict[str, str]]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"フィードのファイルが見つからない: {p}")
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("文字コードを読めなかった（UTF-8 か Shift_JIS にする）")
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def feed_check(row: Optional[Dict[str, Any]] = None, csv_path: str = "", max_rows: int = 20) -> dict:
    if (row is None) == (not csv_path):
        raise ValueError("row（商品 1 行の辞書）か csv_path（CSV・TSV のパス）のどちらか 1 つを渡す")
    try:
        max_rows = max(1, min(int(max_rows), 200))
    except (TypeError, ValueError):
        max_rows = 20
    rows = [row] if row is not None else _read_csv(csv_path)
    if not rows:
        raise ValueError("商品の行が 0 件")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"1 回に点検するのは {MAX_ROWS:,} 行まで（受け取った行: {len(rows):,}）")
    results, all_issues, counts = [], [], {}
    for n, r in enumerate(rows, 1):
        issues = check_row(r)
        all_issues += issues
        for i in issues:
            key = f"{i['属性']}: {i['種類']}"
            counts[key] = counts.get(key, 0) + 1
        if issues:
            results.append({"行": n, "id": _s(r.get("id")) or None, "所見": issues})
    unknown = sorted({str(k).strip().lower() for r in rows for k in r if k and str(k).strip().lower() not in KNOWN})
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "商品 1 行ごと。字数はそのままの文字数",
        "入力": {"行数": len(rows), "出どころ": csv_path or "渡された 1 行"},
        "件数": {"所見のある行": len(results), "所見": len(all_issues), "所見の種類ごと": dict(sorted(counts.items(), key=lambda x: -x[1]))},
        "行": results[:max_rows],
        "知識ベースの表に無い属性（点検していない）": unknown or None,
        "根拠": _cites(all_issues),
        "点検していない決まり": [
            "21 に書かれていない決まり（link・mobile_link など）は点検しない。",
            "画像の寸法（最小 500×500、2027-01-31 から）・中身、着地ページの価格との一致は、ページや画像を開かないので見ていない。",
            "商品の分類で決まる必須（衣料の age_group・gender・color・size、バリエーションの item_group_id、中古の condition、指定が必須の google_product_category）は、分類が分からないので見ていない。",
            "配送（日本は必須）はアカウントの配送設定でも満たせるので、属性が無くても所見にしていない。",
        ],
    }
