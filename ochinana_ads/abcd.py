"""動画のクリエイティブの指針（ABCD ほか）を引く道具と、台本・絵コンテを ABCD の観点で見直す道具。外へは何も送らない。

- 指針は知識ベース 38（動画のクリエイティブ ABCD と公式の指針）の表を、呼ぶたびに読み直す（中身をコードに写さない）。
  表の列「目的」「フォーマット」「尺」で絞り、「確認の問い」をそのまま返す。
- 台本の見直しは判定しない。機械で拾える目印（最初の 5 秒にブランド名が出るか、CTA があるか など）を
  「見つかったもの」「見つからなかったもの」として並べ、確かめる問いと根拠の行を付ける。決めるのは人。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from . import kb

KB_FILE = "38"        # 何を入れるか（ABCD ほか）
HOWTO_FILE = "40"     # どう作るか（企画から入稿・検証までの工程）。無くても 38 だけで動く
PHASES = {
    "企画": ("企画", "計画", "ブリーフ", "brief", "planning"),
    "台本・絵コンテ": ("台本", "絵コンテ", "脚本", "シナリオ", "script", "storyboard"),
    "撮影・素材": ("撮影", "素材", "shooting", "assets", "クリエイター", "ugc"),
    "編集": ("編集", "editing", "字幕", "テロップ", "カット"),
    "音": ("音", "音楽", "ナレーション", "audio", "music", "bgm"),
    "Google の作成ツール": ("作成ツール", "アセットスタジオ", "asset studio", "ツール", "動画の改善", "トリミング"),
    "アップロードと入稿": ("アップロード", "入稿", "upload", "審査", "ポリシー"),
    "検証と改善": ("検証", "改善", "テスト", "実験", "experiment", "入れ替え"),
    "点検表": ("点検表", "チェックリスト", "checklist"),
}
OBJECTIVES = {"認知": ("認知", "awareness", "リーチ", "ブランド認知"), "比較検討": ("比較検討", "consideration", "検討", "視聴"),
              "行動": ("行動", "action", "コンバージョン", "cv", "獲得", "販売", "見込み顧客")}
FORMAT_WORDS = {
    "ショート": ("ショート", "shorts", "short", "縦"), "バンパー": ("バンパー", "bumper", "6秒"),
    "インフィード": ("インフィード", "in-feed", "infeed"), "スキップ可能": ("スキップ可能", "インストリーム", "instream", "skippable"),
    "スキップ不可": ("スキップ不可", "non-skippable"), "デマンド ジェネレーション": ("デマンドジェネレーション", "dg", "demandgen"),
    "P-MAX": ("pmax", "p-max", "performancemax"), "動画リーチ": ("動画リーチ", "videoreach", "vrc"),
    "動画再生回数": ("動画再生回数", "vvc", "videoviews"), "テレビ画面": ("テレビ", "ctv", "tv"),
}
LETTERS = {"A": "A 注目", "B": "B ブランド", "C": "C つながり", "D": "D 行動"}
JUDGE_NOTE = ("判定はしない。「見つかった目印」は台本の文字から機械で拾ったもので、満たしているという意味ではない。"
              "「見つからなかった目印」も、台本に書いていないだけのことがある。最後は問いに人が答える。"
              "Google の「認知の ABCD を 80% 以上」の数え方は公開されていない（38 §8）ので、割合は出さない。")


def _n(s: Any) -> str:
    return re.sub(r"[\s_\-・／/（）()]+", "", unicodedata.normalize("NFKC", str(s or "")).casefold())


def _howto_rows() -> Tuple[Optional[dict], List[dict], Optional[str]]:
    """40（どう作るか）の表。無ければ空で、理由を返す。"""
    try:
        doc = kb.find_doc(HOWTO_FILE)
    except kb.KBError as ex:
        return None, [], str(ex)
    rows = [u for u in doc["units"] if u["type"] == "表の行" and "確認の問い" in u["header"] and "工程" in u["header"]]
    return doc, rows, None if rows else "40 に工程の表（列「工程」「確認の問い」）が見つからない"


def _rows() -> Tuple[dict, List[dict]]:
    doc = kb.find_doc(KB_FILE)
    rows = [u for u in doc["units"] if u["type"] == "表の行" and "確認の問い" in u["header"]]
    if not rows:
        raise kb.KBError("38 に指針の表（列「確認の問い」）が見つからない。メモの形が変わった可能性がある")
    return doc, rows


def _pick(value: str, table: Dict[str, tuple], what: str) -> Optional[str]:
    if not value:
        return None
    v = _n(value)
    for name, words in table.items():
        if v == _n(name) or any(v == _n(w) for w in words):
            return name
    for name, words in table.items():
        if any(len(_n(w)) >= 2 and (_n(w) in v or v in _n(w)) for w in (name, *words)):
            return name
    raise ValueError(f"{what}は {' / '.join(table)} のどれか（受け取った値: {value!r}）")


def _len_ok(cell: str, sec: Optional[float]) -> bool:
    if sec is None or not cell or "全部" in cell:
        return True
    c = unicodedata.normalize("NFKC", cell)
    m = re.search(r"(\d+(?:\.\d+)?)\s*〜\s*(\d+(?:\.\d+)?)", c)
    if m:
        return float(m.group(1)) <= sec <= float(m.group(2))
    m = re.search(r"〜\s*(\d+(?:\.\d+)?)", c)
    if m:
        return sec <= float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*秒超", c)
    if m:
        return sec > float(m.group(1))
    return True


def _match_cell(cell: str, want: Optional[str], words: Dict[str, tuple]) -> bool:
    if not want:
        return True
    c = _n(cell)
    if not c or "全部" in cell:
        return True
    return any(_n(w) and _n(w) in c for w in (want, *words.get(want, ())))


def _view(doc: dict, u: dict, cell_chars: int = 400) -> dict:
    c = u["cells"]
    get = lambda *names: next((c[n] for n in names if n in c), "")  # noqa: E731
    out = {"番号": get("番号")}
    if "工程" in c:
        out.update({"工程": get("工程"), "項目": get("項目")})
    else:
        out.update({"原則": get("原則"), "指針": get("指針（公式の日本語名）", "指針")})
    return {**out, "中身": kb._cut(get("中身", "やること"), cell_chars),
            "目的": get("目的"), "フォーマット": get("フォーマット"), "尺": get("尺"), "確認の問い": get("確認の問い"),
            "印": get("印"), "出典": get("出典"),
            "根拠": {"ファイル": doc["rel"], "節": u["section"], "行番号": u["line"], "確認日": doc["date"]}}


def guide(objective: str = "", format: str = "", length_sec: Optional[float] = None, letter: str = "", query: str = "",
          include_numbers: bool = True, max_results: int = 40, max_chars: int = 20000, phase: str = "") -> dict:
    obj = _pick(objective, OBJECTIVES, "目的")
    ph = _pick(phase, PHASES, "工程")
    fmt = _pick(format, FORMAT_WORDS, "フォーマット")
    sec = None if length_sec in (None, "") else float(length_sec)  # type: ignore[arg-type]
    let = None
    if letter:
        let = unicodedata.normalize("NFKC", letter).strip().upper()[:1]
        if let not in LETTERS:
            raise ValueError("letter は A・B・C・D のどれか（注目・ブランド・つながり・行動）")
    doc, rows = _rows()
    hdoc, hrows, herr = _howto_rows()
    words = [w for w in re.split(r"\s+", unicodedata.normalize("NFKC", query or "").strip()) if w]
    out, numbers = [], []
    pool = ([] if ph else [(doc, u) for u in rows]) + [(hdoc, u) for u in hrows if not ph or _match_cell(u["cells"].get("工程", ""), ph, PHASES)]
    for src, u in pool:
        c = u["cells"]
        if not _match_cell(c.get("目的", ""), obj, OBJECTIVES):
            continue
        if fmt and not _match_cell(c.get("フォーマット", ""), fmt, FORMAT_WORDS):
            continue
        if not _len_ok(c.get("尺", ""), sec):
            continue
        if let and not (c.get("原則", "").startswith(let) or c.get("原則", "") == "全体"):
            continue
        if words and not all(kb.norm(w) in u["n"] for w in words):
            continue
        out.append(_view(src, u))
    if include_numbers:
        for u in doc["units"]:
            if u["type"] == "表の行" and "数字" in u["header"] and "誰が・いつ・どれだけで測ったか" in u["header"]:
                if words and not all(kb.norm(w) in u["n"] for w in words):
                    continue
                c = u["cells"]
                numbers.append({"番号": c.get("番号"), "中身": c.get("中身"), "数字": c.get("数字"),
                                "誰が・いつ・どれだけ": c.get("誰が・いつ・どれだけで測ったか"), "印": c.get("印"), "出典": c.get("出典"),
                                "根拠": {"ファイル": doc["rel"], "行番号": u["line"]}})
    total = len(out)
    out, cut = kb._budget(out[: max(1, min(int(max_results), 200))], int(max_chars))
    notes = []
    if fmt == "ショート":
        notes.append("ショートは ABCD の大事な順が「つながり → 注目 → ブランド → 行動」（38 の ショート-順）")
    if fmt == "バンパー":
        notes.append("バンパーの手引きは 2017・2021 年の資料で、いまのヘルプに同じ記事は無い（38 §8）")
    if obj == "認知" and (not fmt or fmt == "動画リーチ"):
        notes.append("認知の動画リーチは「認知の ABCD の 80% 以上」が公式の目安。数え方は公開されていない（38 の 認知-80）")
    if herr:
        notes.append(f"作り方の工程（40）は引けなかった: {herr}。38 の指針だけを返した")
    if ph and not hrows:
        notes.append("工程で絞ったが、40 が無いので何も返せない")
    if cut or total > len(out):
        notes.append(f"{total} 件のうち {len(out)} 件を返した（max_results・max_chars で増やせる）")
    return {
        "対象アカウント": "なし（知識ベース）",
        "期間": "指定なし",
        "単位": "尺は秒",
        "入力": {"目的": obj or "指定なし（全部）", "フォーマット": fmt or "指定なし（全部）", "尺（秒）": sec, "原則": LETTERS.get(let or "", "指定なし"),
                "工程": ph or "指定なし（38 の指針と 40 の工程の全部）", "語": words or None},
        "工程ごとの件数": _count_phases(out),
        "指針": out,
        "公式が示す数字": numbers if include_numbers else "省いた",
        "注記": notes or None,
        "読み方": "指針は作り方の目安で、守れば成果が出るという約束ではない。数字は Google か Google の委託の調べで、条件の記載が無いものが多い（仮説として扱う）。"
                  "印が △ の行は公式の中で記述が食い違う（38 §7）。",
        "出どころ": [{"ファイル": d["rel"], "確認日": d["date"]} for d in (doc, hdoc) if d],
    }


def _count_phases(rows: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = r.get("工程") or "中身（ABCD ほか、38）"
        out[k] = out.get(k, 0) + 1
    return out


# ================= 台本・絵コンテの見直し =================

SCENE_KEYS = {
    "開始": ("開始", "start", "from", "始まり"), "終了": ("終了", "end", "to", "終わり"),
    "画": ("画", "映像", "visual", "picture", "絵", "shot"), "文字": ("文字", "text", "テキスト", "スーパー", "テロップ", "字幕", "super"),
    "音声": ("音声", "ナレーション", "セリフ", "せりふ", "audio", "voice", "vo", "声"), "音": ("音", "音楽", "効果音", "sound", "music", "bgm"),
    "行動": ("行動", "cta", "ボタン", "行動を促すフレーズ"),
}
_TIME = re.compile(r"^\s*[\[(（]?\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?\s*[-〜~–ー－]\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?\s*[\])）]?\s*[:：]?\s*(.*)$")
_LABEL = re.compile(r"(画|映像|文字|テロップ|字幕|音声|ナレーション|セリフ|音楽|効果音|音|行動|CTA)\s*[:：]")
CTA_WORDS = ("詳しく", "今すぐ", "相談", "申し込", "購入", "予約", "登録", "ダウンロード", "検索", "チェック", "見る", "試す", "問い合わせ",
             "お問い合わせ", "資料請求", "無料", "はこちら", "クリック", "タップ", "learn more", "shop", "sign up", "buy", "book", "visit")
HUMAN_WORDS = ("人", "顔", "手", "出演", "話す", "語る", "笑", "声", "店員", "お客", "家族", "子ども", "子供", "私", "カメラ目線", "クリエイター", "インフルエンサー")
BRAND_ASSETS = {"ロゴ": ("ロゴ", "logo"), "商品": ("商品", "製品", "パッケージ", "パック"), "色": ("色面", "ブランドの色", "ブランドカラー", "色"),
                "決まり文句": ("タグライン", "キャッチ", "合言葉", "決まり文句"), "キャラクター": ("キャラクター", "マスコット"),
                "音のロゴ": ("サウンドロゴ", "音のロゴ", "ジングル")}
ENGAGE_WORDS = ("？", "?", "驚", "意外", "笑", "冗談", "クイズ", "なぜ", "実は", "ひみつ", "秘密", "まさか")


def _scene_from_dict(d: dict) -> dict:
    out: Dict[str, Any] = {}
    low = {str(k).strip().casefold(): v for k, v in d.items()}
    for key, names in SCENE_KEYS.items():
        for n in (key, *names):
            if n.casefold() in low:
                out[key] = low[n.casefold()]
                break
    for k in ("画", "文字", "音声", "音", "行動"):
        v = out.get(k, "")
        out[k] = "　".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v or "")
    try:
        out["開始"] = float(out.get("開始")) if out.get("開始") not in (None, "") else None
        out["終了"] = float(out.get("終了")) if out.get("終了") not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("場面の 開始・終了 は秒の数字で渡す") from None
    return out


def _scenes_from_text(text: str) -> List[dict]:
    scenes = []
    for ln in str(text).splitlines():
        m = _TIME.match(ln)
        if m:
            body = m.group(3)
            sc = {"開始": float(m.group(1)), "終了": float(m.group(2)), "画": "", "文字": "", "音声": "", "音": "", "行動": ""}
            parts = _LABEL.split(body)
            if len(parts) == 1:
                sc["画"] = body
            else:
                sc["画"] = parts[0].strip()
                for lab, val in zip(parts[1::2], parts[2::2]):
                    key = {"映像": "画", "テロップ": "文字", "字幕": "文字", "ナレーション": "音声", "セリフ": "音声", "音楽": "音",
                           "効果音": "音", "CTA": "行動"}.get(lab, lab)
                    sc[key] = (sc[key] + "　" + val.strip()).strip("　")
            scenes.append(sc)
        elif scenes and ln.strip():
            scenes[-1]["画"] = (scenes[-1]["画"] + "　" + ln.strip()).strip("　")
    if not scenes:
        raise ValueError("台本の文から場面を読めなかった。各行を「0-2秒 画: … 文字: … 音声: …」の形で書くか、場面の並び（辞書）で渡す")
    return scenes


def _has(text: str, words) -> List[str]:
    t = unicodedata.normalize("NFKC", text).casefold()
    return [w for w in words if unicodedata.normalize("NFKC", w).casefold() in t]


def _fmt_t(sc: dict) -> str:
    return f"{sc['開始']:g}〜{sc['終了']:g} 秒" if sc.get("開始") is not None and sc.get("終了") is not None else "時刻なし"


def storyboard_check(scenes: Any = None, script: str = "", brand_name: str = "", objective: str = "", format: str = "",
                     length_sec: Optional[float] = None, has_audio: Optional[bool] = None) -> dict:
    if scenes:
        if not isinstance(scenes, list) or not all(isinstance(s, dict) for s in scenes):
            raise ValueError("scenes は場面の並び（[{開始, 終了, 画, 文字, 音声, 行動}, …]）で渡す")
        sc_list = [_scene_from_dict(s) for s in scenes]
    elif script:
        sc_list = _scenes_from_text(script)
    else:
        raise ValueError("scenes（場面の並び）か script（「0-2秒 画: … 文字: …」の形の文）を渡す")
    if not brand_name:
        raise ValueError("brand_name（ブランド名・商品名）を渡す。ブランドの出方を見るのに使う")
    obj = _pick(objective, OBJECTIVES, "目的")
    fmt = _pick(format, FORMAT_WORDS, "フォーマット")
    timed = all(s.get("開始") is not None and s.get("終了") is not None for s in sc_list)
    total = float(length_sec) if length_sec not in (None, "") else (max(s["終了"] for s in sc_list) if timed else None)  # type: ignore[arg-type]
    brand_keys = [brand_name] + [w for w in re.split(r"[\s/／、,]+", brand_name) if len(w) >= 2]
    all_text = lambda s: "　".join([s.get("画", ""), s.get("文字", ""), s.get("音声", ""), s.get("行動", "")])  # noqa: E731

    signals: Dict[str, Tuple[List[str], List[str]]] = {}

    def put(key, found=None, missing=None):
        f, m = signals.setdefault(key, ([], []))
        if found:
            f.append(found)
        if missing:
            m.append(missing)

    first = [s for s in sc_list if timed and s["開始"] < 2] or sc_list[:1]
    put("ABCD-A1", found="最初の場面: " + "／".join(x for x in (first[0].get("画", ""), first[0].get("文字", "")) if x)[:120])
    if any(_has(first[0].get("画", ""), ("ロゴだけ", "社名だけ", "黒画面", "白画面", "前置き"))):
        put("ABCD-A1", missing="最初の場面が前置き（ロゴだけ・無地）に見える")
    n_text = sum(1 for s in sc_list if s.get("文字"))
    n_voice = sum(1 for s in sc_list if s.get("音声"))
    n_sound = sum(1 for s in sc_list if s.get("音"))
    put("ABCD-A2", found=f"画面の文字がある場面 {n_text}/{len(sc_list)}" if n_text else None,
        missing=None if n_text else "画面の文字（スーパー）が台本に無い")
    if n_voice or n_sound:
        put("ABCD-A2", found=f"声がある場面 {n_voice}、音楽・効果音がある場面 {n_sound}")
    if has_audio is False or not (n_voice or n_sound):
        put("ABCD-A2", missing="声・音楽・効果音が無い（無音）")
    put("ABCD-A3", missing="色の差・明るさは台本からは見られない（書き出した動画を小さい画面で見て確かめる）")

    def brand_in(s):  # ブランド名か、ロゴの記述
        return bool(_has(all_text(s), brand_keys) or _has(s.get("画", "") + s.get("文字", ""), ("ロゴ", "logo")))
    early = [s for s in sc_list if timed and s["開始"] < 5]
    if timed:
        if any(brand_in(s) for s in early):
            put("ABCD-B1", found="最初の 5 秒にブランド名が出る（" + "、".join(_fmt_t(s) for s in early if brand_in(s)) + "）")
        else:
            put("ABCD-B1", missing="最初の 5 秒の場面にブランド名・ロゴの記述が無い")
    else:
        put("ABCD-B1", missing="場面に時刻が無いので、最初の 5 秒にブランドが出るかは見られない")
    n_brand = sum(1 for s in sc_list if brand_in(s))
    put("ABCD-B1", found=f"ブランドが出る場面 {n_brand}/{len(sc_list)}" if n_brand else None,
        missing=None if brand_in(sc_list[-1]) else "最後の場面にブランドの記述が無い")
    if any(_has(s.get("音声", ""), brand_keys) for s in sc_list):
        put("ABCD-B2", found="ブランド名を声で言っている")
    else:
        put("ABCD-B2", missing="ブランド名を声で言う場面が無い")
    kinds = [k for k, ws in BRAND_ASSETS.items() if any(_has(all_text(s), ws) for s in sc_list)]
    put("ABCD-B3", found=f"ブランドの要素: {'・'.join(kinds)}" if kinds else None,
        missing=None if len(kinds) >= 2 else "ロゴ以外のブランド要素（商品・色・決まり文句・音など）の記述が少ない")
    humans = sorted({w for s in sc_list for w in _has(s.get("画", "") + s.get("音声", ""), HUMAN_WORDS)})
    put("ABCD-C1", found=f"人の記述: {'・'.join(humans)}" if humans else None, missing=None if humans else "人（顔・手・声・出演者）の記述が無い")
    msgs = [s.get("文字", "") for s in sc_list if s.get("文字")]
    put("ABCD-C2", found=f"画面の文字は {len(msgs)} 場面" + (f"、{total:g} 秒" if total else ""))
    if total and len(msgs) / max(total, 1) > 0.5:
        put("ABCD-C2", missing=f"文字の場面が尺に対して多い（{len(msgs)} 場面／{total:g} 秒）。要点が多すぎないか")
    eng = sorted({w for s in sc_list for w in _has(all_text(s), ENGAGE_WORDS)})
    put("ABCD-C3", found=f"問いかけ・驚きの目印: {'・'.join(eng)}" if eng else None, missing=None if eng else "問いかけ・驚き・笑いの目印が無い")
    last_part = [s for s in sc_list if timed and total and s["終了"] > total * 0.7] or sc_list[-1:]
    cta = [s for s in sc_list if s.get("行動") or _has(s.get("文字", ""), CTA_WORDS)]
    if cta:
        put("ABCD-D1", found="CTA の記述: " + "、".join(f"{_fmt_t(s)}「{(s.get('行動') or s.get('文字'))[:30]}」" for s in cta[:3]))
        if not any(s in last_part for s in cta):
            put("ABCD-D1", missing="CTA が終わりの 3 割の場面に無い")
    else:
        put("ABCD-D1", missing="CTA（してほしい行動）の記述が無い")
    if any(s.get("行動") and _has(s.get("音声", ""), CTA_WORDS + (s.get("行動", ""),)) for s in sc_list) or \
            any(_has(s.get("音声", ""), CTA_WORDS) for s in sc_list):
        put("ABCD-D2", found="CTA を声で言っている")
    else:
        put("ABCD-D2", missing="CTA を声で言う場面が無い")

    # Google 広告が自動で見る 4 属性（38 の G-01）
    g01_found, g01_missing = [], []
    if timed:
        logo_early = [s for s in early if _has(all_text(s), ("ロゴ", "logo")) or brand_in(s)]
        (g01_found if logo_early else g01_missing).append("最初の 5 秒のロゴ" + ("（ブランド名・ロゴの記述あり）" if logo_early else "の記述が無い"))
    (g01_found if n_voice else g01_missing).append("人の声のナレーション" + ("あり" if n_voice else "が無い"))
    g01_missing.append("3 つの比率（16:9・9:16・1:1）は、この台本 1 本では見られない（広告グループに 1 本ずつ入れる）")
    signals["G-01"] = (g01_found, g01_missing)

    doc, rows = _rows()
    by_id = {u["cells"].get("番号", ""): u for u in rows}
    items = []
    for key in ["ABCD-A1", "ABCD-A2", "ABCD-A3", "ABCD-B1", "ABCD-B2", "ABCD-B3", "ABCD-C1", "ABCD-C2", "ABCD-C3", "ABCD-D1", "ABCD-D2", "G-01"]:
        f, m = signals.get(key, ([], []))
        u = by_id.get(key)
        row = {"番号": key, "見つかった目印": f or None, "見つからなかった目印": m or None}
        if u:
            v = _view(doc, u, 200)
            row.update({"指針": v["指針"], "確認の問い": v["確認の問い"], "印": v["印"], "根拠": v["根拠"]})
        else:
            row["根拠"] = "この行がいまの知識ベース（38）に見つからない。メモが直された可能性がある"
        items.append(row)
    # 目的・フォーマット・尺に合わせた追加の問い
    extra = []
    if obj or fmt:
        for u in rows:
            c = u["cells"]
            no = c.get("番号", "")
            if no.startswith("ABCD-") or no == "G-01":
                continue
            purpose, place = c.get("目的", "全部"), c.get("フォーマット", "全部")
            generic_p, generic_f = purpose in ("全部", ""), place in ("全部", "")
            if generic_p and generic_f:
                continue  # 目的もフォーマットも「全部」の行は、中核の 11 項目と重なるので足さない
            if not _match_cell(purpose, obj, OBJECTIVES) or (not generic_p and not obj):
                continue
            if not generic_f and not (fmt and _match_cell(place, fmt, FORMAT_WORDS)):
                continue
            if not _len_ok(c.get("尺", ""), total):
                continue
            v = _view(doc, u, 200)
            extra.append({"番号": no, "指針": v["指針"], "確認の問い": v["確認の問い"], "印": v["印"], "根拠": v["根拠"]})
    missing_all = [f"{it['番号']}: {x}" for it in items for x in (it.get("見つからなかった目印") or [])]
    return {
        "対象アカウント": "なし（知識ベースと台本）",
        "期間": "指定なし",
        "単位": "時刻は秒",
        "入力": {"ブランド名": brand_name, "目的": obj or "指定なし", "フォーマット": fmt or "指定なし", "尺（秒）": total,
                "場面の数": len(sc_list), "時刻つき": timed},
        "読んだ場面": [{"時刻": _fmt_t(s), **{k: s.get(k) for k in ("画", "文字", "音声", "音", "行動") if s.get(k)}} for s in sc_list],
        "まず確かめること": missing_all[:12] or ["機械で拾える目印では、欠けている点は見つからなかった（それでも問いには人が答える）"],
        "中核の ABCD と自動で見られる 4 属性": items,
        "目的・フォーマットに合わせた問い": extra or ("目的・フォーマットを渡すと足す" if not (obj or fmt) else "当たる行が無い"),
        "判定の範囲": JUDGE_NOTE,
        "出どころ": {"ファイル": doc["rel"], "確認日": doc["date"]},
    }
