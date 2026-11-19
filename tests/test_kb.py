"""知識ベースを引く道具。小さな見本のフォルダで確かめ、実物のフォルダがあれば読めることも確かめる。"""
import json
import os
import time
from pathlib import Path

import pytest

from ochinana_ads import kb

RULES_15 = """# 15「細かいルール表」（見本）

- 確認日: 2026-11-16
- 位置づけ: 1 行 1 ルールの早見表
- 凡例: ✅ 公式で確認 ／ △ 食い違う ／ ❓ 記載なし

## 1. 広告とアセットの仕様

### 1-1. レスポンシブ検索広告（RSA）

| 番号 | 対象 | ルール | 印 | 出典 |
|---|---|---|---|---|
| 1-01 | RSA 見出し | 最少 3 本、最大 15 本 | ✅ | H:7684791 |
| 1-03 | RSA 文字数 | 見出し半角 30（全角 15）、説明文半角 90（全角 45） | ✅ | H:7684791 |
| 1-56 | デマンド ジェネレーション 見出し | ヘルプは半角 40、API は表示幅 30 と書く | △ | H:17091672 |

## 2. アセット

| 番号 | 対象 | ルール | 印 | 出典 |
|---|---|---|---|---|
| 2-12 | サイトリンク | リンク文は 1〜25（半角） | ✅ | API:SitelinkAsset.link_text |

## 9. 公式の中で食い違っていた点

| 番号 | 点 | 片方の記述 | もう片方の記述 | 扱い |
|---|---|---|---|---|
| 1-56 | DG の見出し | ヘルプ: 半角 40 | API: 表示幅 30 | 30 以内を 1 本 |
"""

MEMO_05 = """# 05 広告文（見本）

- 確認日: 2026-11-15
- 使い方: 広告文に答える前に読む

## 1. 広告文の数え方

| 項目 | 仕様 | 判定 |
|---|---|---|
| 日本語の数え方 | 全角1文字＝半角2文字ぶん | ✅ |

サイトリンクは **2 個以上** ないと出ない。`公式`

## 10. 細かいルール一覧

| 対象 | ルール | 出典 |
|---|---|---|
| コールアウト | 半角 25 まで | 6079510 |
"""

GLOSSARY = """# 33: 用語集（見本）

- 確認日: 2026-11-17

## 名前が変わった用語の対応表

| 旧称・別の表記 | 今の呼び名（日本語ヘルプ） | いつ・どこで | 備考 | 詳しい所 |
|---|---|---|---|---|
| 部分一致（キーワードのマッチタイプ） | インテント マッチ（英: Broad match） | 日本語ヘルプ | 除外の側は部分一致 ❓ | 04 §1 |

## 4. クリックと費用の指標

| 用語 | 英語 | 略語・別名・旧称 | 意味 | 間違えやすい点 | くわしく |
|---|---|---|---|---|---|
| 平均クリック単価 | Average CPC | CPC、クリック単価 | 費用 ÷ クリック数 | 上限とは別 | L2 |
| 広告費用対効果 | Return on ad spend | ROAS | コンバージョン値 ÷ 費用 | 売上ベース | 公式用語集 |
"""

TIPS = """# T1 検索広告の Tips（見本）

- 確認日: 2026-11-17

## 1. キーワード

| 番号 | 場面 | Tip | 根拠の種類 | 誰の主張か・出典 | 時期 | 条件・反対意見 |
|---|---|---|---|---|---|---|
| 1-7 | 少額・立ち上げ | 最初はフレーズ一致と完全一致で始める | 経験則 | 実務家 A | 2026-03 | 部分一致を勧める声もある |
| 1-8 | 除外キーワード | 除外は週に 1 回見直す | 経験則 | 実務家 B | 2026-05 | — |
"""

TREE = """# L2 ロジックツリー: クリック単価（見本）

- 確認日: 2026-11-17
- 対象の指標: ① 平均クリック単価（CPC）② 費用（FEE）

## 1. 平均クリック単価（CPC）

### 1-2. まず切り分ける順番

1. 構成が変わったかを先に見る

### 1-3. 「上がったとき」の木（CPC-上）

| 番号 | 分類 | 要因 | なぜそうなるか | 確かめ方 | 打ち手 | 副作用・注意 | 根拠 |
|---|---|---|---|---|---|---|---|
| CPC-上-01 | ① 計測 | 構成が変わった | 加重平均だから | キャンペーン表を並べる | 分けて報告する | — | 0-2（`推測`） |
| CPC-上-07 | ② 入札 | 予算を増やした | 倍率が上がる | 変更履歴 | 段階的に上げる | — | 00 §4-1（`公式`） |

### 1-4. 「下がったとき」の木（CPC-下）

| 番号 | 分類 | 要因 | なぜそうなるか | 確かめ方 | 打ち手 | 副作用・注意 | 根拠 |
|---|---|---|---|---|---|---|---|
| CPC-下-01 | ① 計測 | 安い区分が増えた | 加重平均だから | 区分ごとに見る | 分けて報告する | — | 0-2（`推測`）、T1 7-7（`経験則`） |

## 2. 費用（FEE）

| 番号 | 分類 | 要因 | なぜそうなるか | 確かめ方 | 打ち手 | 副作用・注意 | 根拠 |
|---|---|---|---|---|---|---|---|
| FEE-上-01 | 予算 | 日予算を上げた | — | 変更履歴 | 戻す | — | 公式 02§6 |
"""

KATA = """# R1 運用の型: 点検（見本）

- 書いた日: 2026-11-17

## 1. 毎週の点検

予算のペースを月の累計で見る。
"""

UPPER = """# AI 最大化設定（見本）

- 確認日: 2026-11-10

## 1. 何か

AI Max は検索キャンペーンの設定。
"""


@pytest.fixture()
def sample(tmp_path, monkeypatch):
    base = tmp_path / "媒体仕様メモ"
    root = base / "Google広告の基礎"
    (root / "tips").mkdir(parents=True)
    (root / "ロジックツリー").mkdir()
    (root / "運用の型").mkdir()
    (root / "15_細かいルール表.md").write_text(RULES_15, encoding="utf-8")
    (root / "05_広告文.md").write_text(MEMO_05, encoding="utf-8")
    (root / "33_用語集.md").write_text(GLOSSARY, encoding="utf-8")
    (root / "tips" / "T1_検索広告.md").write_text(TIPS, encoding="utf-8")
    (root / "ロジックツリー" / "L2_クリック単価.md").write_text(TREE, encoding="utf-8")
    (root / "運用の型" / "R1_点検.md").write_text(KATA, encoding="utf-8")
    (root / "_下書き.md").write_text("# 読まない\n", encoding="utf-8")
    (root / "閲覧.html").write_text("<p>読まない</p>", encoding="utf-8")
    (base / "Google_AI最大化設定.md").write_text(UPPER, encoding="utf-8")
    monkeypatch.setenv(kb.ENV_KB_DIR, str(root))
    return root


def test_一覧は種類と確認日と一言つき(sample):
    out = kb.overview()
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    rows = {r["番号"]: r for r in out["行"]}
    assert set(rows) == {"15", "05", "33", "T1", "L2", "R1", "Google_AI最大化設定"}  # _下書き と html は読まない
    assert rows["T1"]["種類"] == "Tips" and rows["L2"]["種類"] == "ロジックツリー" and rows["R1"]["種類"] == "運用の型（体系）"
    assert rows["15"]["確認日"] == "2026-11-16" and rows["R1"]["確認日"] == "2026-11-17"  # 「書いた日」も読む
    assert rows["15"]["一言"] == "1 行 1 ルールの早見表"
    assert rows["Google_AI最大化設定"]["ファイル"] == "../Google_AI最大化設定.md"
    assert rows["15"]["表の行"] == 5 and rows["15"]["字数"] > 0
    assert out["件数"]["種類ごと"] == {"知識メモ": 4, "Tips": 1, "ロジックツリー": 1, "運用の型（体系）": 1}
    assert kb.overview("Tips")["件数"]["ファイル"] == 1


def test_フォルダが無ければ場所の指し方を返す(tmp_path, monkeypatch):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(tmp_path / "none"))
    with pytest.raises(kb.KBError, match="ADS_KB_DIR"):
        kb.overview()


def test_探す_ANDで見出しを先に_表は1行1件(sample):
    out = kb.search("サイトリンク")
    assert out["件数"]["当たり"] >= 2
    forms = [r["形"] for r in out["行"]]
    assert "表の行" in forms and "本文" in forms
    row = next(r for r in out["行"] if r["形"] == "表の行")
    assert row["行の中身"]["番号"] == "2-12" and row["印"] == ["✅"] and row["確認日"] == "2026-11-16"
    assert row["節"] == "2. アセット" and row["行"] > 0
    body = next(r for r in out["行"] if r["形"] == "本文")
    assert body["根拠の種類"] == ["公式"]
    head = kb.search("数え方")
    assert head["行"][0]["形"] == "見出し" and "kb_read" in head["行"][0]["読むには"]
    assert [r["行の中身"]["番号"] for r in kb.search("RSA 15 本")["行"]] == ["1-01"]  # 全部を含む行だけ
    assert kb.search("ｒｓａ　１５　本")["件数"]["当たり"] == 1  # 全角・大文字でも当たる
    assert kb.search("デマンドジェネレーション")["件数"]["当たり"] >= 1  # 空白の有無を問わない
    assert kb.search("見出し", file="15")["件数"]["ファイルごとの当たり（上位）"].keys() == {"15_細かいルール表.md"}
    assert all(r["種類"] == "Tips" for r in kb.search("除外", kind="Tips")["行"])
    none = kb.search("存在しない語")
    assert none["件数"]["当たり"] == 0 and "未確認" in none["注記"][0]
    with pytest.raises(kb.KBError):
        kb.search("  ")
    with pytest.raises(kb.KBError, match="種類"):
        kb.search("a", kind="ブログ")
    with pytest.raises(kb.KBError, match="ファイル"):
        kb.search("a", file="99")


def test_探す_件数と文字数の上限(sample):
    out = kb.search("見出し", max_results=1)
    assert len(out["行"]) == 1 and out["件数"]["当たり"] > 1
    tiny = kb.search("見出し", max_chars=1)
    assert len(tiny["行"]) == 1 and "打ち切った" in tiny["注記"][0]


def test_読む_節と目次と続き(sample):
    toc = kb.read("15", toc_only=True)
    assert [t["見出し"] for t in toc["目次"]][:2] == ["1. 広告とアセットの仕様", "1-1. レスポンシブ検索広告（RSA）"]
    sec = kb.read("15", "1-1")
    assert sec["節"].endswith("1-1. レスポンシブ検索広告（RSA）") and "| 1-03 |" in sec["本文"] and "2-12" not in sec["本文"]
    assert kb.read("15", "公式の中で食い違って")["本文"].startswith("## 9.")
    part = kb.read("15", "", max_chars=100)
    assert len(part["本文"]) == 100 and part["続き"] == "offset=100 で続きを読む"
    nxt = kb.read("15", "", max_chars=100, offset=100)
    assert nxt["本文"] and nxt["本文"] != part["本文"]
    assert kb.read("運用の型/R1")["題名"] == "R1 運用の型: 点検（見本）"
    with pytest.raises(kb.KBError, match="節"):
        kb.read("15", "99-9")


def test_ルール_対象と印で引き_食い違いを添える(sample):
    out = kb.rules(target="RSA")
    assert [r["番号"] for r in out["行"]] == ["1-01", "1-03"]
    r = out["行"][1]
    assert r["ルール"].startswith("見出し半角 30") and r["印"] == "✅" and r["出典"] == "H:7684791"
    assert r["ファイル"] == "15_細かいルール表.md" and r["確認日"] == "2026-11-16"
    tri = kb.rules(mark="△")["行"]
    assert len(tri) == 1 and tri[0]["食い違いの記述"]["片方の記述"] == "ヘルプ: 半角 40"
    other = kb.rules("コールアウト")["行"][0]  # 各メモの「細かいルール一覧」（印の列なし）も引く
    assert other["印"] == "（印の列なし）" and other["ファイル"] == "05_広告文.md"
    with pytest.raises(kb.KBError, match="印"):
        kb.rules(mark="○")


def test_ロジックツリー_名前と略号と別名と番号で引く(sample):
    out = kb.logic_tree("クリック単価", "上がった")
    assert [m["略号"] for m in out["当たった指標"]] == [["CPC"]]
    assert [r["番号"] for r in out["行"]] == ["CPC-上-01", "CPC-上-07"]
    row = out["行"][0]
    assert row["要因"] == "構成が変わった" and row["確かめ方"] and row["打ち手"] and row["根拠の種類"] == ["推測"]
    assert row["扱い"].startswith("根拠に経験則・推測")
    assert "構成が変わったかを先に見る" in out["まず切り分ける順番"][0]["本文"]
    assert [r["番号"] for r in kb.logic_tree("cpc", "下")["行"]] == ["CPC-下-01"]
    assert kb.logic_tree("Average CPC")["件数"]["当たり"] == 3  # 用語集の英語名・別名から。向きを省くと両方
    assert kb.logic_tree("費用", "増えた")["行"][0]["番号"] == "FEE-上-01"
    one = kb.logic_tree(id="cpc-上-07")
    assert one["行"][0]["要因"] == "予算を増やした" and one["行"][0]["ファイル"] == "ロジックツリー/L2_クリック単価.md"
    listing = kb.logic_tree()
    assert {m["指標"] for m in listing["指標の一覧"]} == {"平均クリック単価（CPC）", "費用（FEE）"}
    with pytest.raises(kb.KBError, match="向き"):
        kb.logic_tree("CPC", "横ばい")
    with pytest.raises(kb.KBError, match="指標"):
        kb.logic_tree("インプレッション シェア")
    with pytest.raises(kb.KBError, match="番号"):
        kb.logic_tree(id="CPC-上-99")


def test_用語集_旧称と略語から引く(sample):
    out = kb.glossary("部分一致")
    assert out["行"][0]["形"] == "名前が変わった用語" and "インテント マッチ" in out["行"][0]["今の呼び名（日本語ヘルプ）"]
    assert out["行"][0]["印"] == ["❓"]
    roas = kb.glossary("roas")["行"][0]
    assert roas["用語"] == "広告費用対効果" and roas["当たり方"] == "名前・略語・旧称が一致"
    assert kb.glossary("クリック単価")["行"][0]["用語"] == "平均クリック単価"
    assert kb.glossary("知らない語")["件数"]["当たり"] == 0


def test_Tipsは必ず仮説と添える(sample):
    out = kb.tips("除外")
    assert out["必ず伝えること"].startswith("Tips は") and "仮説" in out["必ず伝えること"]
    assert [r["番号"] for r in out["行"]] == ["1-8"] and out["行"][0]["扱い"] == "仮説"
    assert [r["番号"] for r in kb.tips(scene="少額")["行"]] == ["1-7"]
    with pytest.raises(kb.KBError):
        kb.tips()


def test_主張を照らす_判定はせず並べる(sample):
    out = kb.check_claim("レスポンシブ検索広告（RSA）の見出しは半角30文字まで")
    assert out["主張の中の数字"] == ["30"]
    rules = out["関係しそうな記述"]["ルール表"]
    assert rules[0]["行の中身"]["番号"] == "1-03" and rules[0]["同じ数字"] == ["30"]
    assert "判定しない" in out["この道具がしないこと"]
    tips = kb.check_claim("除外キーワードは週に1回見直す")["関係しそうな記述"]["Tips（仮説）"]
    assert tips and tips[0]["扱い"] == "仮説"
    with pytest.raises(kb.KBError):
        kb.check_claim("")


def test_ファイルが直されたら読み直す(sample):
    first = kb.rules(target="サイトリンク")["行"][0]["ルール"]
    p = sample / "15_細かいルール表.md"
    text = p.read_text(encoding="utf-8").replace("リンク文は 1〜25（半角）", "リンク文は 1〜30（半角）")
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert first != kb.rules(target="サイトリンク")["行"][0]["ルール"]
    (sample / "tips" / "T2_入札.md").write_text(TIPS.replace("T1", "T2"), encoding="utf-8")
    assert kb.overview()["件数"]["種類ごと"]["Tips"] == 2  # 足されたファイルもすぐ出る


def test_根拠の行を引く_数字の食い違いも知らせる(sample, monkeypatch):
    ok = kb.cite("15", "1-03", expect=("30", "90"))
    assert ok["確かめ"] == "数字は知識ベースの行と合う" and ok["印"] == ["✅"] and ok["確認日"] == "2026-11-16"
    changed = kb.cite("15", "1-03", expect=("35",))
    assert "見当たらない" in changed["確かめ"]
    assert "見つからない" in kb.cite("15", "9-99")["確かめ"]
    assert kb.cite("05", "日本語の数え方")["確かめ"] == "知識ベースの行を引いた"
    assert kb.cite_line("05", "2 個以上")["確かめ"] == "知識ベースの記述と合う"
    monkeypatch.setenv(kb.ENV_KB_DIR, "/nonexistent/kb")
    assert "読めなかった" in kb.cite("15", "1-03")["確かめ"]  # 知識ベースが無くても落ちない


def test_知識ベースを書き換えない(sample):
    before = {p: p.read_bytes() for p in sample.rglob("*") if p.is_file()}
    kb.overview()
    kb.search("見出し")
    kb.rules()
    kb.logic_tree("CPC")
    kb.check_claim("見出しは 30 文字")
    assert before == {p: p.read_bytes() for p in sample.rglob("*") if p.is_file()}


# ---- 実物の知識ベース（あるときだけ） ----

REAL = Path(kb.DEFAULT_KB_DIR)
real = pytest.mark.skipif(not REAL.is_dir(), reason="実物の知識ベースが無い環境")


@real
def test_実物_全部のファイルを読めて速い(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    kb._cache.clear()
    t = time.time()
    out = kb.overview()
    assert out["件数"]["ファイル"] >= 40 and time.time() - t < 10
    assert {"知識メモ", "Tips", "ロジックツリー"} <= set(out["件数"]["種類ごと"])
    t = time.time()
    s = kb.search("サイトリンク 説明文")
    assert s["件数"]["当たり"] > 0 and time.time() - t < 2  # 2 回目からは索引を使う


@real
def test_実物_ルールとツリーと用語集を引ける(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    assert any(r["番号"] == "1-03" for r in kb.rules(target="RSA", file="15")["行"])
    assert any("食い違いの記述" in r for r in kb.rules(mark="△", file="15", max_results=50)["行"])
    assert kb.logic_tree(id="CPC-上-07")["行"][0]["番号"] == "CPC-上-07"
    assert kb.logic_tree("ROAS", "下")["件数"]["当たり"] > 0
    assert kb.glossary("ROAS")["件数"]["当たり"] > 0
    assert kb.tips("除外キーワード")["件数"]["当たり"] > 0
    c = kb.check_claim("レスポンシブ検索広告の見出しは全角で15文字まで")
    assert any(r["行の中身"].get("番号") == "1-03" for r in c["関係しそうな記述"]["ルール表"])
    assert len(json.dumps(kb.search("見出し", max_chars=3000), ensure_ascii=False)) < 6000
