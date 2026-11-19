"""広告文の文字数と、画像の寸法の点検。外へは何も送らない。

仕様の数字は、知識ベース（15・16・12 ほか）で確かめた値を下の表に置き、どのメモのどの行が根拠かを一緒に持つ。
呼ぶたびに、その行をいまの知識ベースから引き直し、数字がまだ書いてあるかを確かめて返す（kb.cite）。
公式の中で食い違う数字（△）は、両方で判定して、その旨を返す。
"""
from __future__ import annotations

import os
import re
import struct
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import kb

# ================= 広告文 =================

# 1 項目の決まり: 上限（半角換算）、食い違うもう片方の上限、本数（最少, 最大）、根拠の行
F = dict
AD_SPECS: Dict[str, dict] = {
    "レスポンシブ検索広告": {
        "別名": ("rsa", "検索広告", "レスポンシブ検索広告", "responsive search ad"),
        "項目": {
            "見出し": F(max=30, count=(3, 15), dup=True, refs=[("15", "1-03", ("30",)), ("15", "1-01", ("15",))]),
            "説明文": F(max=90, count=(2, 4), dup=True, refs=[("15", "1-03", ("90",)), ("15", "1-02", ("4",))]),
            "パス1": F(max=15, count=(0, 1), refs=[("15", "1-04", ("15",))]),
            "パス2": F(max=15, count=(0, 1), needs="パス1", refs=[("15", "1-04", ("パス 1",))]),
        },
    },
    "サイトリンク": {
        "別名": ("sitelink", "sitelinks", "サイトリンク"),
        "項目": {
            "リンク文": F(max=25, count=(0, None), dup=True, refs=[("15", "2-12", ("25",)), ("15", "2-14", ("2 個以上",))]),
            "説明文1": F(max=35, unit_note="上限の単位（半角か表示幅か）は 15 の行に書かれていない。半角換算で数えた",
                       refs=[("15", "2-13", ("35",))]),
            "説明文2": F(max=35, unit_note="同上", refs=[("15", "2-13", ("35",))]),
        },
    },
    "コールアウト": {
        "別名": ("callout", "callouts", "コールアウト"),
        "項目": {"コールアウト": F(max=25, dup=True, refs=[("15", "2-19", ("25",))])},
    },
    "構造化スニペット": {
        "別名": ("structured snippet", "snippet", "構造化スニペット", "スニペット"),
        "項目": {"値": F(max=25, count=(3, 10), dup=True, unit_note="上限の単位は 15 の行に書かれていない。半角換算で数えた",
                       refs=[("15", "2-20", ("3〜10", "25"))])},
    },
    "P-MAX": {
        "別名": ("pmax", "p-max", "performance max", "パフォーマンス最大化"),
        "項目": {
            "見出し": F(max=30, count=(3, 15), dup=True, short=15, refs=[("15", "1-30", ("30", "15"))]),
            "長い見出し": F(max=90, count=(1, 5), dup=True, refs=[("15", "1-31", ("90",))]),
            "説明文": F(max=90, count=(2, 5), dup=True, refs=[("15", "1-32", ("90",))]),
            "短い説明文": F(max=None, count=(1, None), refs=[("15", "1-33", ("短い説明文",))],
                         unit_note="文字数の上限は知識ベースで未確認（15 の「10. 未確認の点」）。本数だけ見た"),
            "ビジネスの名前": F(max=25, count=(1, 1), refs=[("15", "1-34", ("25",))]),
            "パス1": F(max=15, count=(0, 1), refs=[("15", "1-36", ("15",))]),
            "パス2": F(max=15, count=(0, 1), needs="パス1", refs=[("15", "1-36", ("パス 2",))]),
        },
    },
    "デマンド ジェネレーション": {
        "別名": ("dg", "demand gen", "demandgen", "デマンドジェネレーション", "デマンド ジェネレーション"),
        "項目": {
            "見出し": F(max=40, count=(1, 5), dup=True, short=30,
                       refs=[("15", "1-56", ("40",)), ("15", "1-57", ("30",))]),
            "長い見出し": F(max=90, count=(0, None), dup=True,
                         unit_note="12 の表は 90 文字と書くが、15 の「未確認の点」は DG の長い見出しの文字数を未確認としている",
                         refs=[("12", "長い見出し（動画）", ("90",))]),
            "説明文": F(max=90, count=(1, 5), dup=True, refs=[("15", "1-58", ("90",))]),
            "ビジネスの名前": F(max=25, count=(1, 1), refs=[("15", "1-59", ("25",))]),
        },
    },
    "レスポンシブ ディスプレイ広告": {
        "別名": ("rda", "responsive display ad", "レスポンシブディスプレイ広告", "ディスプレイ広告"),
        "項目": {
            "短い見出し": F(max=30, count=(1, 5), dup=True, refs=[("16", "短い広告見出し", ("30",)), ("15", "1-67", ("30",))]),
            "長い見出し": F(max=90, count=(1, 1), refs=[("16", "長い広告見出し", ("90",))]),
            "説明文": F(max=90, alt_max=80, count=(1, 5), dup=True,
                       conflict="上限は API が 90、英語ヘルプが 80 と書く（16 の 5-1 △）。両方で判定した",
                       refs=[("16", "説明文", ("90", "80"), "5-1")]),
            "ビジネスの名前": F(max=25, count=(0, 1), refs=[("16", "ビジネスの名前", ("25",), "5-1")]),
        },
    },
    # ---- 動画広告（2026-11-18。根拠は 39 の §4） ----
    "動画リーチ（インストリーム）": {
        "別名": ("動画リーチ", "video reach", "インストリーム", "instream", "in-stream", "動画広告", "video", "スキップ可能",
                "バンパー", "bumper", "動画リーチキャンペーン"),
        "項目": {
            "長い見出し": F(max=90, count=(1, 1), refs=[("39", "T-01", ("90",))],
                         unit_note="管理画面の名前は「長い広告見出し」（必須）。ヘルプは「広告見出し」と呼ぶ（39 の T-01）"),
            "説明文": F(max=90, count=(0, 1), refs=[("39", "T-02", ("90",))]),
            "行動を促すフレーズ": F(max=10, count=(0, 1), refs=[("39", "T-03", ("10",))]),
            "見出し": F(max=30, count=(0, 1), needs="行動を促すフレーズ", refs=[("39", "T-04", ("30",))],
                       unit_note="管理画面の「広告見出し」は、行動を促すフレーズに付く見出し（最大 30。CTA を入れたときだけ出る）。主の見出しは「長い見出し」（最大 90）"),
            "表示URL": F(max=255, count=(0, 1), refs=[("39", "T-05", ("255",))]),
        },
    },
    "ショート（動画リーチ）": {
        "別名": ("ショート", "shorts", "youtube shorts", "ショート広告"),
        "項目": {
            "長い見出し": F(max=90, rec_max=40, count=(0, 1),
                         refs=[("39", "T-01", ("90",)), ("39", "T-06", ("40",))]),
            "説明文": F(max=90, count=(0, None), dup=True, refs=[("39", "T-06", ("90",))]),
            "行動を促すフレーズ": F(max=10, count=(0, 1), refs=[("39", "T-03", ("10",))]),
        },
    },
    "インフィード動画広告": {
        "別名": ("インフィード", "in-feed", "infeed", "インフィード動画", "in-feed video"),
        "項目": {
            "見出し": F(max=40, count=(0, 2), unit_note="見出しは 2 行。1 行ずつ 1 本として渡す（1 行あたりの上限）",
                       refs=[("39", "T-07", ("40",))]),
            "説明文": F(max=35, count=(0, 2), unit_note="説明文は 2 行。1 行ずつ 1 本として渡す（1 行あたりの上限）",
                       refs=[("39", "T-08", ("35",))]),
        },
    },
    "マストヘッド": {
        "別名": ("マストヘッド", "masthead"),
        "項目": {
            "見出し": F(max=40, count=(0, 1), refs=[("39", "T-09", ("40",))]),
            "説明文": F(max=60, count=(0, 1), refs=[("39", "T-09", ("60",))]),
            "行動を促すフレーズ": F(max=15, count=(0, 1), refs=[("39", "T-09", ("15",))]),
        },
    },
    "デマンド ジェネレーション（動画広告）": {
        "別名": ("dg動画", "dgvideo", "demand gen video", "デマンドジェネレーション動画", "デマンド ジェネレーション 動画",
                "デマンドジェネレーション（動画）", "デマンド ジェネレーション（動画広告）"),
        "項目": {
            "見出し": F(max=40, count=(1, 5), rec=3, dup=True, refs=[("39", "T-10", ("40",)), ("12", "見出し（画像・動画）", ("40",))]),
            "長い見出し": F(max=90, count=(1, None), rec=3, dup=True, refs=[("39", "T-10", ("90",)), ("12", "長い見出し（動画）", ("90",))]),
            "説明文": F(max=90, count=(1, 5), rec=3, dup=True, refs=[("39", "T-10", ("90",))]),
            "ビジネスの名前": F(max=25, count=(1, 1), refs=[("39", "T-10", ("25",))]),
        },
    },
}

FIELD_ALIASES = {
    "headline": "見出し", "headlines": "見出し", "見出し": "見出し",
    "description": "説明文", "descriptions": "説明文", "説明文": "説明文",
    "path1": "パス1", "パス1": "パス1", "パス 1": "パス1", "path2": "パス2", "パス2": "パス2", "パス 2": "パス2",
    "long_headline": "長い見出し", "long_headlines": "長い見出し", "長い見出し": "長い見出し",
    "short_headline": "短い見出し", "short_headlines": "短い見出し", "短い見出し": "短い見出し",
    "short_description": "短い説明文", "short_descriptions": "短い説明文", "短い説明文": "短い説明文",
    "business_name": "ビジネスの名前", "ビジネスの名前": "ビジネスの名前", "ビジネス名": "ビジネスの名前",
    "link_text": "リンク文", "link_texts": "リンク文", "リンク文": "リンク文", "リンクテキスト": "リンク文",
    "description1": "説明文1", "説明文1": "説明文1", "説明文 1": "説明文1",
    "description2": "説明文2", "説明文2": "説明文2", "説明文 2": "説明文2",
    "callout": "コールアウト", "callouts": "コールアウト", "コールアウト": "コールアウト", "コールアウト文": "コールアウト",
    "values": "値", "value": "値", "値": "値", "header": "ヘッダー", "ヘッダー": "ヘッダー",
    "text": "文", "texts": "文", "文": "文",
    "cta": "行動を促すフレーズ", "call_to_action": "行動を促すフレーズ", "行動を促すフレーズ": "行動を促すフレーズ",
    "行動を促す文": "行動を促すフレーズ", "ボタン": "行動を促すフレーズ",
    "cta_headline": "CTAの見出し", "ctaの見出し": "CTAの見出し",
    "行動を促すフレーズの見出し": "CTAの見出し", "行動を促すフレーズに付く見出し": "CTAの見出し",
    "display_url": "表示URL", "表示url": "表示URL", "表示 url": "表示URL", "表示URL": "表示URL",
}

EDITORIAL_REFS = [("15", "7-17", ("記号の乱用",)), ("29", "29-09", ("連続",)), ("29", "29-10", ("飾り",)),
                  ("29", "29-11", ("絵文字", "半角カタカナ")), ("34", "E13", ("スペース",))]
COUNT_REF = ("05_広告文", "日本語の数え方", ("全角1文字＝半角2文字",))
DECORATIVE = set("★☆■□◆◇●○◎►▶▷◀◁▲△▼▽♪♫※〓☆♡♥✓✔✕✖")
SENTENCE_PUNCT = set("、。，．,.!！?？・:：;；")


def width(text: str) -> int:
    """Google の数え方: 全角 1 文字は半角 2 文字ぶん。あいまい幅（○、→ など）も 2 と数える。"""
    n = 0
    for ch in str(text):
        n += 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1
    return n


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    if ch in DECORATIVE:
        return False
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o == 0xFE0F or 0x2B00 <= o <= 0x2BFF


def editorial_flags(text: str) -> List[str]:
    """編集基準で引っかかりやすい点。機械で見える範囲の目印で、審査の結果を予言するものではない。"""
    t = str(text)
    out = []
    for m in re.finditer(r"([^\w\s])\1+", t):
        out.append(f"同じ記号の繰り返し「{m.group(0)}」")
    for m in re.finditer(r"[、。，．,.!！?？・:：;；]{2,}", t):
        if len(set(m.group(0))) > 1:
            out.append(f"句読点・記号の連続「{m.group(0)}」")
    deco = sorted({ch for ch in t if ch in DECORATIVE})
    if deco:
        out.append(f"飾りに見える記号 {''.join(deco)}（目立たせるための記号は不可。★・【】の扱いは 29 で ❓）")
    emoji = sorted({ch for ch in t if _is_emoji(ch)})
    if emoji:
        out.append(f"絵文字 {''.join(emoji)}（無効な文字）")
    hk = sorted({ch for ch in t if 0xFF61 <= ord(ch) <= 0xFF9F})
    if hk:
        out.append(f"半角カタカナ {''.join(hk)}（無効な文字）")
    if t != t.strip():
        out.append("先頭か末尾に空白")
    if "　" in t:
        out.append("全角スペース（強調のための全角スペースは不可）")
    if re.search(r"[ \t]{2,}", t):
        out.append("空白が 2 つ以上続く")
    if re.search(r"(?<!\S)\S(?: \S){2,}(?!\S)", t):
        out.append("1 文字ずつ空白で区切っている（「今 だ け」の形）")
    return out


def _dup_key(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)).casefold())


def _pick_type(ad_type: str) -> Tuple[str, Optional[dict]]:
    t = re.sub(r"[\s_\-]+", "", unicodedata.normalize("NFKC", str(ad_type or "")).casefold())
    if t in ("", "数えるだけ", "count", "count_only", "なし"):
        return "数えるだけ", None
    for name, spec in AD_SPECS.items():
        keys = {re.sub(r"[\s_\-]+", "", unicodedata.normalize("NFKC", a).casefold()) for a in (*spec["別名"], name)}
        if t in keys:
            return name, spec
    raise ValueError(f"広告の種類は {' / '.join(AD_SPECS)} / 数えるだけ のどれか（受け取った値: {ad_type!r}）")


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return ["" if x is None else str(x) for x in v]
    raise ValueError("各項目の文は、文字列か文字列の並びで渡す")


def _cites(refs) -> List[dict]:
    out = []
    for r in refs:
        file, key, expect = r[0], r[1], r[2]
        section = r[3] if len(r) > 3 else ""
        out.append(kb.cite(file, key, section=section, expect=expect))
    return out


def ad_text_check(ad_type: str, fields: Dict[str, Any]) -> dict:
    name, spec = _pick_type(ad_type)
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields に {項目: 文の並び} を渡す（例: {\"見出し\": [\"…\"], \"説明文\": [\"…\"]}）")
    given: Dict[str, List[str]] = {}
    unknown = []
    for k, v in fields.items():
        key = FIELD_ALIASES.get(str(k).strip().casefold(), FIELD_ALIASES.get(str(k).strip()))
        if name == "レスポンシブ ディスプレイ広告" and key == "見出し":
            key = "短い見出し"
        if key == "CTAの見出し":  # 動画リーチの「行動を促すフレーズに付く見出し」＝管理画面の「広告見出し」
            key = "見出し" if spec is not None and "見出し" in spec["項目"] and "行動を促すフレーズ" in spec["項目"] else None
        if spec is not None and key == "見出し" and "見出し" not in spec["項目"] and "長い見出し" in spec["項目"]:
            key = "長い見出し"  # ショートなど、主の見出しが「長い見出し」の種類
        if spec is not None and key not in spec["項目"] and key != "ヘッダー":
            unknown.append(str(k))
            continue
        given[key or str(k)] = given.get(key or str(k), []) + _as_list(v)
    results, findings, refs_used = [], [], []
    items = spec["項目"] if spec else {k: F(max=None) for k in given}
    for field, rule in items.items():
        texts = given.get(field, [])
        refs_used += rule.get("refs", [])
        seen: Dict[str, int] = {}
        short_ok = False
        for i, text in enumerate(texts, 1):
            w = width(text)
            row: Dict[str, Any] = {"項目": field, "番号": i, "文": text, "文字数（半角換算）": w, "文字数（そのまま）": len(text)}
            mx, alt = rule.get("max"), rule.get("alt_max")
            if mx is not None:
                row["上限"] = mx if alt is None else {"片方": mx, "もう片方": alt}
                if alt is None:
                    row["残り"] = mx - w
                    row["判定"] = "収まる" if w <= mx else f"超過（{w - mx} 超える）"
                else:
                    lo, hi = min(mx, alt), max(mx, alt)
                    row["判定"] = ("どちらでも収まる" if w <= lo else
                                 f"{hi} なら収まるが {lo} だと超過（公式の中で食い違う）" if w <= hi else f"どちらでも超過（{w - hi} 超える）")
                if w > (mx if alt is None else min(mx, alt)):
                    limit = mx if alt is None else f"{mx} か {alt}"
                    findings.append(f"{field} {i}: {row['判定']}（{w}／上限 {limit}）")
            if rule.get("short") and w <= rule["short"]:
                short_ok = True
            if not text.strip():
                row.setdefault("気になる点", []).append("空の文")
            flags = editorial_flags(text)
            k = _dup_key(text)
            if rule.get("dup", spec is None) and k:
                if k in seen:
                    flags.append(f"{field} {seen[k]} と同じ文（重複）")
                else:
                    seen[k] = i
            if flags:
                row.setdefault("気になる点", []).extend(flags)
                findings.append(f"{field} {i}: " + "、".join(flags))
            results.append(row)
        if spec is None:
            continue
        lo, hi = rule.get("count", (0, None))
        n = len([t for t in texts if t.strip()])
        if n < lo:
            findings.append(f"{field}: {n} 本。最少 {lo} 本")
        if hi is not None and n > hi:
            findings.append(f"{field}: {n} 本。最大 {hi} 本")
        if rule.get("rec") and texts and n < rule["rec"]:
            findings.append(f"{field}: {n} 本。ヘルプは {rule['rec']} 本以上を求めている（推奨）")
        if rule.get("rec_max"):
            over = [i for i, t in enumerate(texts, 1) if width(t) > rule["rec_max"]]
            if over:
                findings.append(f"{field} {over}: 推奨の 半角 {rule['rec_max']} を超える（上限内なので入稿はできる。推奨の目安）")
        if rule.get("short") and texts and not short_ok:
            findings.append(f"{field}: 半角 {rule['short']} 以内の文が 1 本も無い（1 本以上入れる決まり・推奨がある）")
        if rule.get("needs") and texts and any(t.strip() for t in texts) and not any(t.strip() for t in given.get(rule["needs"], [])):
            findings.append(f"{field} だけを入れることはできない（{rule['needs']} が先に要る）")
        if rule.get("conflict") and texts:
            findings.append(f"{field}: {rule['conflict']}")
        if rule.get("unit_note") and texts:
            findings.append(f"{field}: {rule['unit_note']}")
    if name == "サイトリンク":
        d1, d2 = given.get("説明文1", []), given.get("説明文2", [])
        for i in range(max(len(d1), len(d2))):
            a = d1[i].strip() if i < len(d1) else ""
            b = d2[i].strip() if i < len(d2) else ""
            if bool(a) != bool(b):
                findings.append(f"サイトリンク {i + 1}: 説明文は 2 行とも入れるか、両方空にする（片方だけは不可）")
        links = [t for t in given.get("リンク文", []) if t.strip()]
        if 0 < len(links) < 2:
            findings.append("サイトリンクは 2 個以上ないと出ない")
    if name == "構造化スニペット" and "ヘッダー" in given:
        findings.append("ヘッダーは用意された一覧から選ぶ（一覧との照合はしていない。15 の 2-20）")
    if unknown:
        findings.append(f"この種類に無い項目は数えていない: {unknown}（使える項目: {list(spec['項目']) if spec else []}）")
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "文字数は半角換算（全角 1 文字＝半角 2）。上限も半角換算",
        "入力": {"種類": name, "項目ごとの本数": {k: len(v) for k, v in given.items()}},
        "所見": findings or ["上限・本数・編集基準の目印で、気になる点は見つからなかった"],
        "行": results,
        "数え方": {"決まり": "全角 1 文字は半角 2 文字ぶん。半角の英数字・記号は 1。あいまい幅の文字（○、→ など）も 2 と数えた",
                 "根拠": kb.cite(*COUNT_REF[:2], expect=COUNT_REF[2])},
        "根拠": _cites(refs_used) if spec else [],
        "編集基準の根拠": _cites(EDITORIAL_REFS),
        "点検の範囲": "文字数・本数・機械で見える編集基準の目印（記号の繰り返し・句読点の連続・飾りの記号・絵文字・半角カタカナ・不自然な空白・重複）だけ。"
                  "意味が通るか、誇張・商標・法規（31）・業種のポリシーは見ていない。審査の結果を保証しない。",
    }


# ================= 画像 =================

S = dict
IMAGE_SPECS: List[dict] = [
    # レスポンシブ ディスプレイ広告（16 の 5-1、15 の 1-68）
    S(fmt="レスポンシブ ディスプレイ広告", slot="横長画像", ratio=(1.91, 1), min=(600, 314), rec=(1200, 628), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("16", "横長画像 1.91:1", ("600×314", "5,120"), "5-1"), ("16", "ファイル形式", ("JPG",), "5-1")]),
    S(fmt="レスポンシブ ディスプレイ広告", slot="スクエア画像", ratio=(1, 1), min=(300, 300), rec=(1200, 1200), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("16", "スクエア画像 1:1", ("300×300", "5,120"), "5-1")]),
    S(fmt="レスポンシブ ディスプレイ広告", slot="縦長画像", ratio=(9, 16), min=(600, 1067), rec=(900, 1600), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("16", "縦長画像 9:16", ("600×1,067",), "5-1"), ("16", "D-69", ("5,120",))]),
    S(fmt="レスポンシブ ディスプレイ広告", slot="ロゴ（横長）", ratio=(4, 1), min=(512, 128), rec=(1200, 300), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("16", "ロゴ 横長 4:1", ("512×128",), "5-1")]),
    S(fmt="レスポンシブ ディスプレイ広告", slot="ロゴ（スクエア）", ratio=(1, 1), min=(128, 128), rec=(1200, 1200), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("16", "ロゴ スクエア 1:1", ("128×128",), "5-1"), ("16", "D-69", ("5,120",))]),
    # デマンド ジェネレーション（12 の 2-2、15 の 1-61・1-63）
    S(fmt="デマンド ジェネレーション", slot="横長画像", ratio=(1.91, 1), min=(600, 314), max_kb=5120, types=None,
      refs=[("12", "画像", ("600×314", "5,120"), "2-2"), ("15", "1-61", ("600×314",))]),
    S(fmt="デマンド ジェネレーション", slot="スクエア画像", ratio=(1, 1), min=(300, 300), max_kb=5120, types=None,
      refs=[("12", "画像", ("300×300",), "2-2"), ("15", "1-61", ("300×300",))]),
    S(fmt="デマンド ジェネレーション", slot="縦長画像（4:5）", ratio=(4, 5), min=(480, 600), max_kb=5120, types=None,
      refs=[("12", "画像", ("480×600",), "2-2"), ("15", "1-61", ("480×600",))]),
    S(fmt="デマンド ジェネレーション", slot="縦長画像（9:16）", ratio=(9, 16), min=(600, 1067), max_kb=5120, types=None,
      refs=[("12", "画像", ("600×1067",), "2-2")]),
    S(fmt="デマンド ジェネレーション", slot="ロゴ", ratio=(1, 1), min=(128, 128), alt_min=(144, 144), rec=(1200, 1200), max_kb=150, types=None,
      conflict="ロゴの最小はヘルプが 144×144、API が 128×128（15 の 1-63 △）。144 以上なら両方に合う",
      refs=[("12", "ロゴ", ("128×128", "150KB"), "2-2"), ("15", "1-63", ("144×144", "128×128"))]),
    # P-MAX（15 の 1-37〜1-41、1-45）
    S(fmt="P-MAX", slot="横長画像", ratio=(1.91, 1), min=(600, 314), rec=(1200, 628), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("15", "1-37", ("600×314", "5,120")), ("15", "1-45", ("GIF",))]),
    S(fmt="P-MAX", slot="スクエア画像", ratio=(1, 1), min=(300, 300), rec=(1200, 1200), max_kb=5120,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("15", "1-38", ("300×300", "5,120"))]),
    S(fmt="P-MAX", slot="縦長画像", ratio=(4, 5), min=(480, 600), rec=(960, 1200), max_kb=None,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("15", "1-39", ("480×600",))]),
    S(fmt="P-MAX", slot="ロゴ（スクエア）", ratio=(1, 1), min=(128, 128), rec=(1200, 1200), max_kb=None,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("15", "1-41", ("128×128",))]),
    S(fmt="P-MAX", slot="ロゴ（横長）", ratio=(4, 1), min=(512, 128), rec=(1200, 300), max_kb=None,
      types=("JPEG", "PNG"), maybe=("GIF",), refs=[("15", "1-41", ("512×128",))]),
    # 検索の画像アセット（15 の 1-29、05 の 6 章）
    S(fmt="画像アセット（検索）", slot="スクエア画像（必須）", ratio=(1, 1), min=(300, 300), max_kb=None, types=None,
      refs=[("15", "1-29", ("300×300",))]),
    S(fmt="画像アセット（検索）", slot="横長画像（任意）", ratio=(1.91, 1), min=None, max_kb=None, types=None,
      note="横長の最小寸法は知識ベースに無い（比率だけ見た）", refs=[("15", "1-29", ("1.91:1",))]),
]
UPLOAD_SIZES = {  # アップロード型のイメージ広告（16 の 5-2、D-74）
    "スクエア・長方形": [(200, 200), (240, 400), (250, 250), (250, 360), (300, 250), (336, 280), (580, 400)],
    "スカイスクレイパー": [(120, 600), (160, 600), (300, 600), (300, 1050)],
    "ビッグバナー": [(468, 60), (728, 90), (930, 180), (970, 90), (970, 250), (980, 120)],
    "モバイル": [(300, 50), (320, 50), (320, 100)],
}
UPLOAD_MAX_KB = 150
UPLOAD_TYPES = ("GIF", "JPEG", "PNG")
RATIO_TOLERANCE = 0.01
RATIO_REF = ("15", "1-68", ("±1%",))
FORMAT_NAMES = ("レスポンシブ ディスプレイ広告", "デマンド ジェネレーション", "P-MAX", "画像アセット（検索）", "イメージ広告（アップロード）")
TYPE_ALIASES = {"JPG": "JPEG", "JPEG": "JPEG", "PNG": "PNG", "GIF": "GIF", "WEBP": "WEBP", "BMP": "BMP", "TIFF": "TIFF", "TIF": "TIFF"}


def _image_size_from_bytes(head: bytes) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """画像の先頭を読んで形式と幅・高さを返す（Pillow が無くても動く。PNG・GIF・JPEG・WebP）。"""
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return "PNG", w, h
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        w, h = struct.unpack("<HH", head[6:10])
        return "GIF", w, h
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        chunk = head[12:16]
        if chunk == b"VP8X" and len(head) >= 30:
            w = 1 + int.from_bytes(head[24:27], "little")
            h = 1 + int.from_bytes(head[27:30], "little")
            return "WEBP", w, h
        if chunk == b"VP8L" and len(head) >= 25:
            b = head[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return "WEBP", w, h
        if chunk == b"VP8 " and len(head) >= 30:
            w, h = struct.unpack("<HH", head[26:30])
            return "WEBP", w & 0x3FFF, h & 0x3FFF
        return "WEBP", None, None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return "JPEG", w, h
            i += 2 + seg
        return "JPEG", None, None
    if head[:2] == b"BM" and len(head) >= 26:
        w, h = struct.unpack("<ii", head[18:26])
        return "BMP", w, abs(h)
    return None, None, None


def measure(path: str) -> dict:
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        raise ValueError(f"画像のファイルが見つからない: {p}")
    size = p.stat().st_size
    fmt = w = h = None
    how = "ファイルの先頭を読んで測った"
    try:
        from PIL import Image  # type: ignore

        with Image.open(p) as im:
            w, h = im.size
            fmt = TYPE_ALIASES.get((im.format or "").upper(), im.format)
            how = "Pillow で測った"
    except Exception:
        with p.open("rb") as f:
            head = f.read(256 * 1024)
        fmt, w, h = _image_size_from_bytes(head)
    if not w or not h:
        raise ValueError(f"画像の幅と高さを読めなかった（形式: {fmt or '不明'}）。width と height を数字で渡す")
    return {"ファイル": str(p), "形式": fmt, "幅": w, "高さ": h, "容量（KB）": round(size / 1024, 1), "測り方": how}


def _ratio_ok(w: int, h: int, ratio: Tuple[float, float]) -> bool:
    return abs((w / h) / (ratio[0] / ratio[1]) - 1) <= RATIO_TOLERANCE


def image_spec_check(width: Optional[int] = None, height: Optional[int] = None, size_kb: Optional[float] = None,
                     file_format: str = "", path: str = "", formats: Optional[List[str]] = None) -> dict:
    measured = None
    if path:
        measured = measure(path)
        width, height = measured["幅"], measured["高さ"]
        size_kb = measured["容量（KB）"]
        file_format = file_format or (measured["形式"] or "")
    try:
        w, h = int(width), int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("width と height を数字（px）で渡すか、path で画像のファイルを渡す") from None
    if w <= 0 or h <= 0:
        raise ValueError("幅と高さは 1 以上の数字で渡す")
    kb_size = None if size_kb in (None, "") else float(size_kb)  # type: ignore[arg-type]
    ftype = TYPE_ALIASES.get(str(file_format).strip().upper().lstrip("."), str(file_format).strip().upper() or None)
    wanted = None
    if formats:
        wanted = set()
        for f in formats:
            nf = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(f)).casefold())
            hits = [n for n in FORMAT_NAMES if nf in re.sub(r"\s+", "", n.casefold()) or
                    (nf in ("rda", "ディスプレイ") and n.startswith("レスポンシブ")) or (nf in ("dg",) and n.startswith("デマンド")) or
                    (nf in ("pmax",) and n == "P-MAX") or (nf in ("イメージ広告", "バナー", "upload") and n.startswith("イメージ"))]
            if not hits:
                raise ValueError(f"フォーマットは {' / '.join(FORMAT_NAMES)} のどれか（受け取った値: {f!r}）")
            wanted.update(hits)
    fits, near, ratio_miss = [], [], []
    for spec in IMAGE_SPECS:
        if wanted and spec["fmt"] not in wanted:
            continue
        name = f"{spec['fmt']}／{spec['slot']}"
        if not _ratio_ok(w, h, spec["ratio"]):
            ratio_miss.append(name)
            continue
        problems, notes = [], []
        if spec.get("min"):
            mw, mh = spec["min"]
            if w < mw or h < mh:
                problems.append(f"小さすぎる（最小 {mw}×{mh}）")
            alt = spec.get("alt_min")
            if alt and (w < alt[0] or h < alt[1]) and not (w < mw or h < mh):
                notes.append(spec["conflict"])
        if spec.get("note"):
            notes.append(spec["note"])
        if spec.get("max_kb") is not None:
            if kb_size is None:
                notes.append(f"容量を渡していないので、容量の上限（{spec['max_kb']:,} KB）は見ていない")
            elif kb_size > spec["max_kb"]:
                problems.append(f"容量が大きすぎる（{kb_size:,} KB。上限 {spec['max_kb']:,} KB）")
        if spec.get("types") is not None:
            if not ftype:
                notes.append("形式を渡していないので、形式は見ていない")
            elif ftype in spec.get("maybe", ()):
                notes.append(f"{ftype} はヘルプとAPIで扱いが食い違う（ヘルプは JPG・PNG、API は GIF も可）。事前にテスト入稿")
            elif ftype not in spec["types"]:
                problems.append(f"形式 {ftype} は使えない（{'・'.join(spec['types'])}）")
        else:
            notes.append("形式の決まりは知識ベースに無い（見ていない）")
        row = {"フォーマット": spec["fmt"], "枠": spec["slot"], "比率": f"{spec['ratio'][0]}:{spec['ratio'][1]}",
               "判定": "合う" if not problems else "合わない", "理由": problems or None,
               "推奨の寸法": f"{spec['rec'][0]}×{spec['rec'][1]}" if spec.get("rec") else None,
               "注記": notes or None, "根拠": _cites(spec["refs"])}
        (fits if not problems else near).append(row)
    upload = None
    if not wanted or "イメージ広告（アップロード）" in wanted:
        group = next((g for g, sizes in UPLOAD_SIZES.items() if (w, h) in sizes), None)
        problems, notes = [], []
        if not group:
            problems.append("決まった寸法（20 種）のどれでもない")
        if kb_size is None:
            notes.append(f"容量を渡していないので、上限（{UPLOAD_MAX_KB} KB）は見ていない")
        elif kb_size > UPLOAD_MAX_KB:
            problems.append(f"容量が大きすぎる（{kb_size:,} KB。上限 {UPLOAD_MAX_KB} KB）")
        if not ftype:
            notes.append("形式を渡していないので、形式は見ていない")
        elif ftype not in UPLOAD_TYPES:
            problems.append(f"形式 {ftype} は使えない（GIF・JPG・PNG）")
        upload = {"フォーマット": "イメージ広告（アップロード）", "枠": group, "判定": "合う" if not problems else "合わない",
                  "理由": problems or None, "注記": notes or None,
                  "根拠": _cites([("16", "D-74", ("150 KB",)), ("16", "形式", ("150 KB",), "5-2")])}
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "寸法は px、容量は KB（1 KB＝1,024 バイト）",
        "入力": {"幅": w, "高さ": h, "比率（幅÷高さ）": round(w / h, 4), "容量（KB）": kb_size, "形式": ftype,
                "フォーマットの絞り込み": sorted(wanted) if wanted else "指定なし（全部）"},
        "測った結果": measured,
        "合う枠": fits,
        "比率は合うが条件を満たさない枠": near,
        "比率が合わない枠": ratio_miss,
        "イメージ広告（アップロード）": upload or "絞り込みの対象外",
        "比率の許容": {"幅": f"±{RATIO_TOLERANCE:.0%}", "根拠": kb.cite(*RATIO_REF[:2], expect=RATIO_REF[2]),
                    "注記": "±1% はレスポンシブ ディスプレイ広告の決まり。ほかのフォーマットの許容幅は知識ベースに無い（同じ幅で見た）"},
        "点検の範囲": "寸法・比率・容量・形式だけ。画像の中身（文字の重ね、ロゴ、余白、ポリシー）は見ていない。",
    }
