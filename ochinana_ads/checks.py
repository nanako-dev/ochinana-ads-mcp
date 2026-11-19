"""点検 10 個。1 項目 1 関数、点検 ID と版つき。API は呼ばず、fetch が作った辞書だけを見る。

- 結果は 3 つ: 「所見あり」「問題なし」「判定できない」。材料が無いときに黙って合格にしない。
- 所見には 重さ（大・中・小）、根拠の数字、直し方の案、「AI の提案（未承認）」の印を付ける。
- 直し方の案は principles.md の順番に並べる。入札・予算に触れる案は最後に「それでも不足なら」として置く。
- 閾値は下の定数。最初の置き値なので、運用者が直す（直したら、その点検の版を上げる）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import convert

# ---- 閾値（置き値） ----
MIN_HEADLINES = 10
MIN_DESCRIPTIONS = 4
RECOMMENDED_COUNT = {"SITELINK": 4, "CALLOUT": 4}
ZERO_CLICK_MIN_IMPRESSIONS = 3        # クリック 0 のまま、これ以上表示された語句を候補にする
ZERO_CLICK_SHARE_MEDIUM = 0.30        # 候補の表示が、見えている語句の表示のこの割合以上なら「中」
MANY_CLICKS_NO_CONVERSION = 100       # これだけクリックがあって成果 0 件なら「大」（計測を疑う）
LCP_POOR, LCP_OK = 4.0, 2.5           # メイン画像の表示（秒）
FCP_POOR, FCP_OK = 3.0, 1.8           # 最初の表示（秒）
RANK_LOST = ((0.50, "大"), (0.30, "中"), (0.10, "小"))
BUDGET_LOST = ((0.30, "中"), (0.10, "小"))
MAX_LIST = 10

CONVERSION_BIDDING = {"MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS"}
MAIN_EXTENSIONS = ("SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "AD_IMAGE")
OPINION = "AI の提案（未承認）"
BID_KIND = "入札・予算"
WEIGHT_ORDER = {"大": 0, "中": 1, "小": 2}

BIDDING_JA = {
    "MAXIMIZE_CONVERSIONS": "コンバージョン数の最大化",
    "MAXIMIZE_CONVERSION_VALUE": "コンバージョン値の最大化",
    "TARGET_CPA": "目標コンバージョン単価",
    "TARGET_ROAS": "目標広告費用対効果",
    "TARGET_SPEND": "クリック数の最大化",
    "MAXIMIZE_CLICKS": "クリック数の最大化",
    "MANUAL_CPC": "個別クリック単価",
    "TARGET_IMPRESSION_SHARE": "目標インプレッション シェア",
}
EXTENSION_JA = {
    "SITELINK": "サイトリンク", "CALLOUT": "コールアウト", "STRUCTURED_SNIPPET": "構造化スニペット",
    "AD_IMAGE": "画像", "CALL": "電話番号", "PRICE": "価格", "PROMOTION": "プロモーション",
    "BUSINESS_NAME": "ビジネスの名前", "BUSINESS_LOGO": "ビジネスのロゴ", "LEAD_FORM": "リードフォーム",
    "MOBILE_APP": "アプリ",
}
STRENGTH_JA = {"EXCELLENT": "優良", "GOOD": "良好", "AVERAGE": "平均的", "POOR": "低い",
               "PENDING": "判定待ち", "NO_ADS": "広告なし"}


@dataclass(frozen=True)
class Check:
    id: str
    version: str
    name: str
    fn: Callable

    def _base(self, target: Optional[str]) -> dict:
        return {"点検": self.id, "版": self.version, "名前": self.name, "対象": target}

    def finding(self, target: str, weight: str, summary: str, evidence: dict, fixes: List[tuple]) -> dict:
        assert weight in WEIGHT_ORDER
        return {**self._base(target), "結果": "所見あり", "重さ": weight, "所見": summary,
                "根拠": evidence, "直し方の案": order_fixes(fixes), "誰の意見": OPINION}

    def ok(self, target: Optional[str], evidence: Optional[dict] = None) -> dict:
        out = {**self._base(target), "結果": "問題なし"}
        if evidence:
            out["根拠"] = evidence
        return out

    def unknown(self, target: Optional[str], reason: str) -> dict:
        return {**self._base(target), "結果": "判定できない", "理由": reason}


CHECKS: List[Check] = []


def check(id: str, version: str, name: str):
    def deco(fn):
        CHECKS.append(Check(id, version, name, fn))
        return fn
    return deco


def order_fixes(fixes: List[tuple]) -> List[dict]:
    """直し方の案を並べる。入札・予算の案は必ず最後に回し、「それでも不足なら」を付ける。"""
    first = [f for f in fixes if f[0] != BID_KIND]
    last = [f for f in fixes if f[0] == BID_KIND]
    out = []
    for kind, text in first:
        out.append({"順": len(out) + 1, "種類": kind, "案": text})
    for kind, text in last:
        out.append({"順": len(out) + 1, "種類": kind,
                    "案": f"それでも不足なら: {text}（必ず運用者の承認を取る）"})
    return out


# ---- 共通の小道具 ----

def normalize(text: str) -> str:
    """全角・半角、大文字・小文字をそろえる。"""
    return unicodedata.normalize("NFKC", str(text)).casefold()


def tokens(keyword: str) -> List[str]:
    """キーワードを語に分ける（空白区切り）。一致方式の記号は外す。"""
    cleaned = re.sub(r'[+"\[\]]', " ", normalize(keyword))
    return [t for t in cleaned.split() if t]


def enabled_campaigns(snap: dict, search_only: bool = False) -> List[dict]:
    rows = [c for c in snap.get("campaigns", []) if c["status"] == "ENABLED"]
    return [c for c in rows if c["channel"] == "SEARCH"] if search_only else rows


def enabled_ad_groups(snap: dict, campaign_id: int) -> List[dict]:
    return [g for g in snap.get("ad_groups", []) if g["campaign_id"] == campaign_id and g["status"] == "ENABLED"]


def enabled_rsas(snap: dict, ad_group_id: int) -> List[dict]:
    return [a for a in snap.get("ads", []) if a["ad_group_id"] == ad_group_id and a["status"] == "ENABLED"
            and a["type"] == "RESPONSIVE_SEARCH_AD"]


def enabled_keywords(snap: dict, ad_group_id: int) -> List[dict]:
    return [k for k in snap.get("keywords", []) if k["ad_group_id"] == ad_group_id and k["status"] == "ENABLED"]


def _no_campaign(c: Check, search_only: bool) -> List[dict]:
    kind = "有効な検索キャンペーン" if search_only else "有効なキャンペーン"
    return [c.unknown(None, f"{kind}が無い")]


def _pct(v: float) -> float:
    return round(v * 100, 1)


def _cut(items: list) -> list:
    return items[:MAX_LIST]


def _me(fn) -> Check:
    return next(c for c in CHECKS if c.fn is fn)


# ---- 点検 ----

@check("C01", "1", "入札が成果ベースなのに、成果が 0 件または目標が入札の対象外")
def c01_bidding_goal(snap: dict) -> List[dict]:
    c = _me(c01_bidding_goal)
    camps = enabled_campaigns(snap)
    if not camps:
        return _no_campaign(c, False)
    setup = snap.get("conversion_setup")
    out = []
    for camp in camps:
        bidding = BIDDING_JA.get(camp["bidding"], camp["bidding"])
        if camp["bidding"] not in CONVERSION_BIDDING:
            out.append(c.ok(camp["name"], {"入札": bidding, "補足": "成果ベースの入札ではないので対象外"}))
            continue
        if setup is None:
            out.append(c.unknown(camp["name"], "成果の目標の設定を読めなかった"))
            continue
        cfg = setup["config"].get(camp["id"]) or {}
        if cfg.get("level") == "CAMPAIGN" and cfg.get("custom_goal"):
            goal = setup["custom_goals"].get(cfg["custom_goal"])
            acts = [setup["actions"].get(rn) for rn in (goal or {}).get("actions", [])]
            live = [a for a in acts if a and a["status"] == "ENABLED"]
            goal_name = f"キャンペーン専用の目標「{goal['name']}」" if goal else "キャンペーン専用の目標（中身を読めない）"
            usable = bool(goal and goal["status"] == "ENABLED" and live)
        else:
            biddable = [g for g in setup["goals"] if g["campaign_id"] == camp["id"] and g["biddable"]]
            pairs = {(g["category"], g["origin"]) for g in biddable}
            live = [a for a in setup["actions"].values()
                    if a["status"] == "ENABLED" and a["primary"] and (a["category"], a["origin"]) in pairs]
            goal_name = "標準の目標"
            usable = bool(live)
        evidence = {
            "入札": bidding, "目標": goal_name,
            "入札の対象になっている成果地点": [a["name"] for a in live],
            "期間の表示回数": camp["impressions"], "期間のクリック数": camp["clicks"],
            "期間の費用（円）": convert.micros_to_yen(camp["cost_micros"]),
            "期間の成果": round(camp["conversions"], 2),
        }
        if not usable:
            out.append(c.finding(
                camp["name"], "大",
                f"入札は「{bidding}」だが、入札の対象になる有効な成果地点が無い。学習の材料が入らない。",
                evidence,
                [("計測", "個人情報を取らない成果地点（保存ボタンのクリックなど）を、このキャンペーンの入札対象の目標に入れる。"),
                 ("計測", "成果地点が実際に発火するかを、自分で 1 回操作して確かめる。"),
                 (BID_KIND, "成果が貯まるまでの入札戦略を見直す")],
            ))
        elif camp["impressions"] == 0:
            out.append(c.unknown(camp["name"], "期間に表示が無いので、成果が 0 件かどうかを判定できない（目標は入札の対象になっている）"))
        elif camp["conversions"] == 0:
            many = camp["clicks"] >= MANY_CLICKS_NO_CONVERSION
            out.append(c.finding(
                camp["name"], "大" if many else "中",
                f"入札は「{bidding}」だが、期間の成果が 0 件（クリック {camp['clicks']} 件）。"
                + ("クリックが多いのに 0 件なので、計測が動いていない疑いがある。" if many else "入札が学習する材料がまだ無い。"),
                evidence,
                [("計測", "成果地点が発火するかを、自分で 1 回操作して確かめる（タグと目標の取り込みの両方）。"),
                 ("品質", "クリックを増やす前に、広告文・表示オプション・キーワードと広告の対応・LP の速度（C02〜C09）を先に直す。"),
                 (BID_KIND, "成果が貯まるまでの入札戦略を見直す")],
            ))
        else:
            out.append(c.ok(camp["name"], evidence))
    return out


@check("C02", "1", "広告の有効性が低い、見出し 10 本未満、説明文 4 本未満")
def c02_ad_copy(snap: dict) -> List[dict]:
    c = _me(c02_ad_copy)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        for g in enabled_ad_groups(snap, camp["id"]):
            target = f"{camp['name']} / {g['name']}"
            ads = enabled_rsas(snap, g["id"])
            if not ads:
                out.append(c.finding(target, "大", "有効なレスポンシブ検索広告が 1 本も無い。", {"有効な広告の数": 0},
                                     [("品質", f"見出し {MIN_HEADLINES} 本以上・説明文 {MIN_DESCRIPTIONS} 本の広告を 1 本作る（下書き→承認）。")]))
                continue
            problems, rows, disapproved = [], [], False
            for ad in ads:
                h, d = len(ad["headlines"]), len(ad["descriptions"])
                p = []
                if ad["approval"] == "DISAPPROVED":
                    p.append("審査で不承認")
                    disapproved = True
                if ad["strength"] == "POOR":
                    p.append("有効性が「低い」")
                if h < MIN_HEADLINES:
                    p.append(f"見出しが {h} 本（{MIN_HEADLINES} 本未満）")
                if d < MIN_DESCRIPTIONS:
                    p.append(f"説明文が {d} 本（{MIN_DESCRIPTIONS} 本未満）")
                rows.append({"広告 ID": ad["ad_id"], "有効性": STRENGTH_JA.get(ad["strength"], ad["strength"]),
                             "見出しの本数": h, "説明文の本数": d, "気になる点": p})
                problems += p
            if problems:
                out.append(c.finding(
                    target, "大" if disapproved else "中", "広告文に足りないところがある: " + "、".join(dict.fromkeys(problems)) + "。",
                    {"広告": rows},
                    [("品質", f"見出しを {MIN_HEADLINES} 本以上、説明文を {MIN_DESCRIPTIONS} 本にする。この広告グループのキーワードの語を見出しに入れる。"),
                     ("品質", "不承認の広告は、理由を管理画面で確かめて直す。")] if disapproved else
                    [("品質", f"見出しを {MIN_HEADLINES} 本以上、説明文を {MIN_DESCRIPTIONS} 本にする。この広告グループのキーワードの語を見出しに入れる。")],
                ))
            else:
                out.append(c.ok(target, {"広告": rows}))
    return out or [c.unknown(None, "有効な広告グループが無い")]


def extension_counts(snap: dict, campaign_id: int, ad_group_id: Optional[int] = None) -> Dict[str, Dict[str, int]]:
    """表示オプションの件数を、付け先（アカウント・キャンペーン・広告グループ）ごとに数える。有効なものだけ。"""
    counts = {t: {"アカウント": 0, "キャンペーン": 0, "広告グループ": 0} for t in MAIN_EXTENSIONS}
    for link in snap.get("asset_links", []):
        t = link["field_type"]
        if t not in counts or link["status"] != "ENABLED":
            continue
        if link["level"] == "account":
            counts[t]["アカウント"] += 1
        elif link["level"] == "campaign" and link["campaign_id"] == campaign_id:
            counts[t]["キャンペーン"] += 1
        elif link["level"] == "ad_group" and ad_group_id is not None and link["ad_group_id"] == ad_group_id:
            counts[t]["広告グループ"] += 1
    return counts


@check("C03", "1", "サイトリンク・コールアウト・構造化スニペット・画像の有無")
def c03_extensions(snap: dict) -> List[dict]:
    c = _me(c03_extensions)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        groups = enabled_ad_groups(snap, camp["id"])
        if not groups:
            out.append(c.unknown(camp["name"], "有効な広告グループが無い"))
            continue
        missing: Dict[str, List[str]] = {}
        few: Dict[str, int] = {}
        table = {}
        for g in groups:
            counts = extension_counts(snap, camp["id"], g["id"])
            table[g["name"]] = {EXTENSION_JA[t]: sum(v.values()) for t, v in counts.items()}
            for t, v in counts.items():
                n = sum(v.values())
                if n == 0:
                    missing.setdefault(t, []).append(g["name"])
                elif n < RECOMMENDED_COUNT.get(t, 0):
                    few[t] = min(few.get(t, n), n)
        camp_level = {EXTENSION_JA[t]: v["キャンペーン"] + v["アカウント"]
                      for t, v in extension_counts(snap, camp["id"]).items()}
        evidence = {"キャンペーンとアカウントに付いている件数": camp_level,
                    "広告グループごとに使える件数（上の階層の分を含む）": table}
        if missing:
            weight = "中" if {"SITELINK", "CALLOUT"} & set(missing) else "小"
            names = "、".join(EXTENSION_JA[t] for t in missing)
            out.append(c.finding(
                camp["name"], weight, f"表示オプションが無い種類がある: {names}。",
                {**evidence, "無い種類と広告グループ": {EXTENSION_JA[t]: _cut(v) for t, v in missing.items()}},
                [("品質", f"{names} を足す（下書き→承認）。サイトリンクとコールアウトは 4 件以上が目安。")],
            ))
        elif few:
            names = "、".join(f"{EXTENSION_JA[t]} {n} 件" for t, n in few.items())
            out.append(c.finding(camp["name"], "小", f"表示オプションの数が少ない: {names}。", evidence,
                                 [("品質", "サイトリンクとコールアウトを 4 件以上にする（下書き→承認）。")]))
        else:
            out.append(c.ok(camp["name"], evidence))
    return out


@check("C04", "1", "キーワードと広告グループのずれ（キーワードの語が広告文・URL に 1 つも出てこない）")
def c04_keyword_ad_mismatch(snap: dict) -> List[dict]:
    c = _me(c04_keyword_ad_mismatch)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        for g in enabled_ad_groups(snap, camp["id"]):
            target = f"{camp['name']} / {g['name']}"
            kws = enabled_keywords(snap, g["id"])
            if not kws:
                continue
            ads = enabled_rsas(snap, g["id"])
            if not ads:
                out.append(c.unknown(target, "有効な検索広告が無いので、照らす相手が無い"))
                continue
            corpus = normalize(" ".join(
                " ".join(a["headlines"] + a["descriptions"] + a["final_urls"] + [a.get("path1") or "", a.get("path2") or ""])
                for a in ads))
            corpus_tight = re.sub(r"\s+", "", corpus)
            off = []
            for k in kws:
                ts = tokens(k["text"])
                long = [t for t in ts if len(t) >= 2] or ts
                if long and not any(t in corpus or t in corpus_tight for t in long):
                    off.append(k["text"])
            evidence = {"有効なキーワードの数": len(kws), "語が 1 つも出てこないキーワードの数": len(off),
                        "該当するキーワード": _cut(off),
                        "判定の範囲": "同じ語が広告文・表示パス・最終 URL に出るかだけを見ている。意図のずれ（例: 入門の検索に別の話題の広告）は見ていない。"}
            if off:
                weight = "大" if len(off) * 2 > len(kws) else "中"
                out.append(c.finding(
                    target, weight, f"有効なキーワード {len(kws)} 個のうち {len(off)} 個は、その語が広告文にも URL にも出てこない。",
                    evidence,
                    [("品質", "語が合う広告グループがあれば、キーワードをそちらへ移す案を作る（下書き→承認）。"),
                     ("品質", "この広告グループに残すなら、キーワードの語を入れた見出しを足す。"),
                     ("品質", "ページの内容と合わない語は、一時停止の案を出す。")],
                ))
            else:
                out.append(c.ok(target, evidence))
    return out or [c.unknown(None, "有効なキーワードが無い")]


@check("C05", "1", "部分一致で語順だけ違う重複")
def c05_broad_duplicates(snap: dict) -> List[dict]:
    c = _me(c05_broad_duplicates)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        groups = {g["id"]: g["name"] for g in enabled_ad_groups(snap, camp["id"])}
        seen: Dict[tuple, List[dict]] = {}
        n = 0
        for k in snap.get("keywords", []):
            if k["ad_group_id"] in groups and k["status"] == "ENABLED" and k["match"] == "BROAD":
                n += 1
                seen.setdefault(tuple(sorted(tokens(k["text"]))), []).append(k)
        dups = [v for key, v in seen.items() if key and len(v) > 1]
        if n == 0:
            out.append(c.ok(camp["name"], {"有効な部分一致のキーワードの数": 0}))
        elif dups:
            sets = [[f"{k['text']}（{groups[k['ad_group_id']]}）" for k in v] for v in dups]
            out.append(c.finding(
                camp["name"], "中", f"部分一致で、語の並びだけが違う（または同じ）キーワードが {len(dups)} 組ある。部分一致では同じ検索を取り合う。",
                {"有効な部分一致のキーワードの数": n, "重複の組の数": len(dups), "重複の組": _cut(sets)},
                [("品質", "各組で 1 個だけ残し、残りを一時停止する案を作る（下書き→承認）。残すのは、広告文と語が合う広告グループにある方。")],
            ))
        else:
            out.append(c.ok(camp["name"], {"有効な部分一致のキーワードの数": n, "重複の組の数": 0}))
    return out


def blocked_by_negative(term: str, negative_text: str, match: str) -> bool:
    """その検索語句が、除外キーワードで止まる見込みか。機械的な近似（日本語の語の区切りは Google と完全には同じでない）。"""
    t, n = normalize(term), normalize(negative_text)
    t_tokens, n_tokens = t.split(), tokens(negative_text)
    if not n_tokens:
        return False
    if match == "EXACT":
        return t_tokens == n_tokens
    if match == "PHRASE":
        return " ".join(n_tokens) in " ".join(t_tokens) or "".join(n_tokens) in "".join(t_tokens)
    return all(tok in t for tok in n_tokens)  # 部分一致の除外: すべての語を含む


def _negatives_for(snap: dict, campaign_id: int, ad_group_id: Optional[int]) -> List[dict]:
    out = []
    for n in snap.get("negatives", []):
        if n.get("account_wide"):
            out.append(n)
        elif n["campaign_id"] == campaign_id and n["ad_group_id"] in (None, ad_group_id):
            out.append(n)
    return out


@check("C06", "1", "クリック 0 のまま表示が積もっている検索語句（除外語句の候補）")
def c06_zero_click_terms(snap: dict) -> List[dict]:
    c = _me(c06_zero_click_terms)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        terms = [t for t in snap.get("search_terms", []) if t["campaign_id"] == camp["id"] and t["impressions"] > 0]
        if not terms:
            out.append(c.unknown(camp["name"], "期間に、見えている検索語句が無い（表示が無いか、少なくて伏せられている）"))
            continue
        total_imp = sum(t["impressions"] for t in terms)
        zero = [t for t in terms if t["clicks"] == 0]
        already, cands = 0, []
        for t in zero:
            if t["impressions"] < ZERO_CLICK_MIN_IMPRESSIONS:
                continue
            if t["status"] in ("EXCLUDED", "ADDED_EXCLUDED") or any(
                blocked_by_negative(t["term"], n["text"], n["match"])
                for n in _negatives_for(snap, camp["id"], t["ad_group_id"])
            ):
                already += 1
                continue
            cands.append(t)
        merged: Dict[str, int] = {}
        registered = set()
        for t in cands:
            merged[t["term"]] = merged.get(t["term"], 0) + t["impressions"]
            if t["status"] == "ADDED":
                registered.add(t["term"])
        ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
        cand_imp = sum(merged.values())
        evidence = {
            "見えている検索語句の数": len(terms), "その表示回数の合計": total_imp,
            "クリック 0 の語句の数": len(zero), "クリック 0 の語句の表示回数": sum(t["impressions"] for t in zero),
            f"候補（クリック 0・表示 {ZERO_CLICK_MIN_IMPRESSIONS} 回以上・まだ除外されていない）の数": len(ranked),
            "候補の表示回数": cand_imp, "候補が表示に占める割合（%）": _pct(cand_imp / total_imp),
            "すでに除外語句で止まる見込みなので外した数": already,
            "候補（表示の多い順）": [{"検索語句": k, "表示回数": v, "キーワードに登録済み": k in registered}
                              for k, v in _cut(ranked)],
            "判定の範囲": "的外れかどうかは見ていない。表示されてクリックが無い語を並べただけなので、除外するかは 1 語ずつ判断する。"
                      "キーワードに登録済みの語は、除外ではなく広告文の側を見直す対象。",
        }
        if ranked:
            weight = "中" if cand_imp / total_imp >= ZERO_CLICK_SHARE_MEDIUM else "小"
            out.append(c.finding(
                camp["name"], weight, f"クリックが無いまま表示されている検索語句が {len(ranked)} 個ある（表示 {cand_imp} 回）。",
                evidence,
                [("品質", "候補を 1 語ずつ見て、ページの内容と合わないものを除外語句にする案を作る（下書き→承認）。"),
                 ("品質", "合う語なのにクリックが無いものは、その語を入れた見出しを足す。")],
            ))
        else:
            out.append(c.ok(camp["name"], evidence))
    return out


@check("C07", "1", "有効なキーワードが全部「検索ボリュームが少ない」広告グループ")
def c07_low_volume_groups(snap: dict) -> List[dict]:
    c = _me(c07_low_volume_groups)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        for g in enabled_ad_groups(snap, camp["id"]):
            target = f"{camp['name']} / {g['name']}"
            kws = enabled_keywords(snap, g["id"])
            if not kws:
                continue
            low = [k["text"] for k in kws if k["serving"] == "RARELY_SERVED"]
            evidence = {"有効なキーワードの数": len(kws), "検索ボリュームが少ないキーワードの数": len(low),
                        "該当するキーワード": _cut(low)}
            if len(low) == len(kws):
                out.append(c.finding(
                    target, "大", f"有効なキーワード {len(kws)} 個が全部「検索ボリュームが少ない」。このままではキーワードからは表示されない。",
                    evidence,
                    [("品質", "実際に検索がある語を足す案を作る（検索数は次の段のキーワード調査の道具で確かめる。下書き→承認）。"),
                     ("品質", "足さない場合、この広告グループはキーワードなしの自動の拡張だけが頼りになる。その前提でよいかを決める。")],
                ))
            else:
                out.append(c.ok(target, evidence))
    return out or [c.unknown(None, "有効なキーワードが無い")]


@check("C08", "1", "地域の判定が「所在地または関心」")
def c08_geo_setting(snap: dict) -> List[dict]:
    c = _me(c08_geo_setting)
    camps = enabled_campaigns(snap)
    if not camps:
        return _no_campaign(c, False)
    out = []
    for camp in camps:
        geo = camp["geo_type"]
        if geo == "PRESENCE_OR_INTEREST":
            out.append(c.finding(
                camp["name"], "中", "地域の判定が「所在地または関心」。対象の地域にいない人にも、その地域に関心があるとみなされると表示される。",
                {"地域の判定": "所在地または関心"},
                [("設定", "対象の地域にいる人だけに出したいなら、「所在地」に変える案を作る（設定の変更なので承認してから）。")],
            ))
        elif geo == "PRESENCE":
            out.append(c.ok(camp["name"], {"地域の判定": "所在地"}))
        else:
            out.append(c.unknown(camp["name"], f"地域の判定の値を読めなかった（{geo}）"))
    return out


@check("C09", "1", "LP のスマホ表示速度")
def c09_landing_page_speed(snap: dict) -> List[dict]:
    c = _me(c09_landing_page_speed)
    audits = snap.get("lp_audits")
    if audits is None:
        return [c.unknown(None, "LP の計測を省いた（include_lp を false にして実行した）")]
    if not audits:
        return [c.unknown(None, "有効な広告の最終 URL が無い")]
    out = []
    for a in audits:
        speed = a.get("表示速度（スマホ）") or {}
        url = a.get("URL")
        if not speed.get("測れたか"):
            out.append(c.unknown(url, f"表示速度を測れなかった: {speed.get('理由', '理由は不明')}"))
            continue
        lcp, fcp = speed.get("メイン画像の表示（秒）"), speed.get("最初の表示（秒）")
        if lcp is None and fcp is None:
            out.append(c.unknown(url, "計測の結果に、表示までの時間が入っていなかった"))
            continue
        evidence = {k: speed.get(k) for k in ("測り方", "総合点（100 点満点）", "最初の表示（秒）", "メイン画像の表示（秒）",
                                                  "描画を止めている資源（上位）", "計測の条件")}
        evidence["目安"] = f"メイン画像の表示 {LCP_OK} 秒以内、最初の表示 {FCP_OK} 秒以内"
        if lcp is not None and lcp > LCP_POOR:
            weight = "大"
        elif (lcp is not None and lcp > LCP_OK) or (fcp is not None and fcp > FCP_POOR):
            weight = "中"
        elif fcp is not None and fcp > FCP_OK:
            weight = "小"
        else:
            out.append(c.ok(url, evidence))
            continue
        fixes = []
        if speed.get("描画を止めている資源（上位）"):
            fixes.append(("品質", "描画を止めている資源（根拠の一覧）の読み込みを後回しにするか、ページに埋め込む。"))
        fixes.append(("品質", "最初の画面に出る画像を軽くし、先に読み込む指定を付ける。"))
        fixes.append(("品質", "直したら、もう一度測って前後を比べる（1 回の計測は数点ぶれる）。"))
        out.append(c.finding(url, weight, f"スマホでの表示が遅い（最初の表示 {fcp} 秒、メイン画像の表示 {lcp} 秒）。", evidence, fixes))
    return out


def _weight_for(value: float, table) -> Optional[str]:
    for threshold, weight in table:
        if value >= threshold:
            return weight
    return None


@check("C10", "1", "広告ランクで失った表示の割合と、予算で失った割合")
def c10_lost_impression_share(snap: dict) -> List[dict]:
    c = _me(c10_lost_impression_share)
    camps = enabled_campaigns(snap, search_only=True)
    if not camps:
        return _no_campaign(c, True)
    out = []
    for camp in camps:
        if camp["impressions"] == 0 or camp.get("rank_lost") is None:
            out.append(c.unknown(camp["name"], "期間に表示が無いので、失った割合を計算できない"))
            continue
        evidence = {
            "期間の表示回数": camp["impressions"],
            "表示できた割合（%）": _pct(camp["search_is"]) if camp.get("search_is") is not None else None,
            "広告ランクで失った割合（%）": _pct(camp["rank_lost"]),
            "予算で失った割合（%）": _pct(camp["budget_lost"]),
            "1 日の予算（円）": convert.micros_to_yen(camp["budget_micros"]),
            "数字の読み方": "Google 広告は 10% 未満を 9.99%、90% 超を 90.01% と返す。",
        }
        found = False
        rank = _weight_for(camp["rank_lost"], RANK_LOST)
        if rank:
            found = True
            out.append(c.finding(
                f"{camp['name']}（広告ランク）", rank,
                f"表示の機会の {_pct(camp['rank_lost'])}% を、広告ランクが足りずに失っている。",
                evidence,
                [("品質", "広告文とキーワードの対応を直す（C02・C04 の所見から）。"),
                 ("品質", "表示オプションをそろえる（C03）。"),
                 ("品質", "LP の表示速度と内容を直す（C09）。"),
                 ("品質", "的外れの検索への表示を減らす（C06）。クリックされない表示が減ると、クリック率の見込みが上がる。"),
                 (BID_KIND, "入札の引き上げ、または入札戦略の見直しを検討する")],
            ))
        budget = _weight_for(camp["budget_lost"], BUDGET_LOST)
        if budget:
            found = True
            out.append(c.finding(
                f"{camp['name']}（予算）", budget,
                f"表示の機会の {_pct(camp['budget_lost'])}% を、1 日の予算が足りずに失っている。",
                evidence,
                [("品質", "先に、的外れの検索への表示とクリックを減らす（C06）。同じ予算で、合う検索に回る分が増える。"),
                 ("品質", "クリック単価を下げる側の打ち手（広告文・LP の品質）を先にやる。"),
                 (BID_KIND, "1 日の予算の増額を検討する")],
            ))
        if not found:
            out.append(c.ok(camp["name"], evidence))
    return out


def run_all(snap: dict) -> List[dict]:
    """点検を全部実行する。点検の中で想定外のエラーが出ても、ほかの点検は続ける。"""
    results: List[dict] = []
    for c in CHECKS:
        try:
            results.extend(c.fn(snap))
        except Exception as ex:  # 黙って合格にしない
            results.append(c.unknown(None, f"点検の実行中にエラーが出た（{type(ex).__name__}）"))
    return results


def summarize(results: List[dict]) -> dict:
    findings = sorted((r for r in results if r["結果"] == "所見あり"),
                      key=lambda r: (WEIGHT_ORDER[r["重さ"]], r["点検"]))
    unknown = [r for r in results if r["結果"] == "判定できない"]
    by_check = []
    for c in CHECKS:
        mine = [r for r in results if r["点検"] == c.id]
        kinds = {r["結果"] for r in mine}
        status = "所見あり" if "所見あり" in kinds else ("判定できない" if "判定できない" in kinds else "問題なし")
        weights = sorted((r["重さ"] for r in mine if r["結果"] == "所見あり"), key=WEIGHT_ORDER.get)
        row = {"点検": c.id, "版": c.version, "名前": c.name, "結果": status}
        if weights:
            row["いちばん重い所見"] = weights[0]
            row["所見の数"] = len(weights)
        if status != "判定できない" and "判定できない" in kinds:
            row["一部は判定できない"] = True
        by_check.append(row)
    return {
        "まとめ": {
            "所見": {w: sum(1 for r in findings if r["重さ"] == w) for w in ("大", "中", "小")},
            "判定できない": len(unknown),
            "問題なし": sum(1 for r in results if r["結果"] == "問題なし"),
        },
        "点検の一覧": by_check,
        "所見": findings,
        "判定できなかったもの": unknown,
    }
