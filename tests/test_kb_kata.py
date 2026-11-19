"""運用の型（体系）・理解度テスト・核となる理解に対応したことを確かめる。

見本のフォルダで、番号の重なり、型NN の引き方、規則・しきい値・式・状態・裁定の引き方、
quiz が既定で正解を返さないこと、90 が無くても動くことを確かめる。実物のフォルダがあれば、そこでも引く。
"""
import csv
import json
import os
from pathlib import Path

import pytest

from ochinana_ads import kata, kb, quiz

MEMO_12 = """# 12 デマンド ジェネレーション（見本）

- 確認日: 2026-11-15

## 2. 画像

| 項目 | 仕様 |
|---|---|
| 画像 | 横長 1.91:1 |
"""

MEMO_00 = """# 00 機械学習の全体像（見本）

- 確認日: 2026-11-16

## 4. 入札

入札は係数と予測で決まる。学習中は最長 7 日。
"""

CHECK_A = "# 94 裏取りの記録（00〜16）\n\n- 確認日: 2026-11-17\n\n## 1. 学習\n\n学習中の表示は最長 7 日。\n"
CHECK_B = "# 94 裏取りの記録（20〜37）\n\n- 確認日: 2026-11-17\n\n## 1. 予算\n\n予算の変更では学習中にならない。\n"

TIPS = """# T2 入札の Tips（見本）

- 確認日: 2026-11-17

## 1. 入札

| 番号 | 場面 | Tip | 根拠の種類 | 誰の主張か・出典 | 時期 | 条件・反対意見 |
|---|---|---|---|---|---|---|
| 3-3 | 予算 | 予算は 20% ずつ動かす。学習期間に注意 | 経験則 | 実務家 A | 2026-03 | — |
"""

TREE = """# L2 ロジックツリー（見本）

- 確認日: 2026-11-17
- 対象の指標: ① 平均クリック単価（CPC）

## 1. 平均クリック単価（CPC）

| 番号 | 分類 | 要因 | なぜそうなるか | 確かめ方 | 打ち手 | 副作用・注意 | 根拠 |
|---|---|---|---|---|---|---|---|
| CPC-上-07 | 入札 | 予算を増やした。学習期間の後 | 倍率が上がる | 変更履歴 | 段階的に | — | 00 §4-1（`公式`） |
"""

KATA_00 = """# 型00 全体の地図（見本）

## §1 各ファイルが答える問い

学習期間の読み方は 型04。
"""

KATA_06 = """# 型06 アカウントの状態の判定（見本）

## §1 状態の定義

| 状態 | 名前 | 定義 | 一言で |
|---|---|---|---|
| 状0 | 止まっている | 広告が出ていない | 輪が回っていない |
| 状4 | 制約で詰まっている | 量が計画に届かない | 出ない |

## §2 決定木

```
問 0  広告は出ているか
      過去 7 日の表示回数が 0
        ├─ はい → 【状0 止まっている】
        └─ いいえ
問 6  量は計画に届いているか
      件数または費用が計画の 90% 未満
        ├─ 届いていない → 【状4 制約で詰まっている】
        └─ 届いている → 【状7 頭打ち（維持）】
```

## §3 各問いの判定基準

### 問 0（状0）

表示回数を見る。

### 問 6（状4）

量の計画と比べる。

## §5 状態ごとの打ち手

| 状態 | 最初にすること | 触ってよい類（型05） | 触ってはいけないもの | 理由 | 送り先 |
|---|---|---|---|---|---|
| 状0 止まっている | 理由を特定して直す | 理由に当たる 1 か所 | それ以外すべて | 効果が 0 | L1 |
| 状4 制約で詰まっている | 制約を 1 つに特定する | 特定した制約 | 特定した制約以外のつまみ | 取り違えると効かない | §4 |
"""

KATA_08 = """# 型08 判断の規則集（見本）

## 使い方

### 1. 状態を決める

| 状態 | 名前 | 主に引く分類 | 触ってはいけないもの |
|---|---|---|---|
| 状0 | 止まっている | 規守（止血） | 止まっている理由以外のすべて |
| 状4 | 制約で詰まっている | 制約を 1 つに特定し、その制約の規則だけ | 特定した制約以外のつまみ |

### 2. 分類を選び、規則を引く

| 接頭辞 | 分類 | 6 類との対応 | 優先の順位 |
|---|---|---|---|
| 規守 | 事故・安全・審査（止血） | 横断 | 1 |
| 規時 | 時間と判定 | 横断 | 4 |
| 規換 | 入札・予算 | 換える | 8 |

## 規時: 時間と判定（2 個）

| ID | もし | なら | 理由 | 例外 | 根拠 | 参照 | 状態 |
|---|---|---|---|---|---|---|---|
| 規時-04 | 目標値または予算を 20% を超えて動かした | 変更日から W を取り直す（閾02） | 取るオークションが変わる（式 4） | — | 〔推論〕裁定 1＋〔経験則〕T2 3-3 | 型04§10 | 状8 |
| 規時-09 | 確定 30 件に届かない | 待つ（閾04） | 振れが大きい（式 6・7） | — | 〔推論〕式 7 | 型04§4 | 状3・状4 |

## 規換: 入札・予算（2 個）

| ID | もし | なら | 理由 | 例外 | 根拠 | 参照 | 状態 |
|---|---|---|---|---|---|---|---|
| 規換-12 | 予算で制限されている（閾12）。効率は目標内 | 予算を 1 回 20% 以内で上げる（閾08） | 予算は費用の上限 | — | 〔経験則〕T2 3-3 | 型06§4 | 状4・状6 |
| 規換-15 | ステータスが「制限付き（入札単価の上限）」 | 上限を外す。外せないなら 1 回 20% 以内で上げ、7 日見る | 上限は自動最適化を制限する | 表示がほぼ 0 なら規守-01 | 〔公式〕02§13＋〔推論〕7 日 | 型06§4-2 | 状0・状4 |

## 規守: 事故・安全・審査（1 個）

| ID | もし | なら | 理由 | 例外 | 根拠 | 参照 | 状態 |
|---|---|---|---|---|---|---|---|
| 規守-01 | 過去 7 日の表示回数が 0 | 止まっている理由を上から確かめる | 出ていない | — | 〔公式〕 | 型06§3 | 状0 |

## 規記: 記録（1 個）

| ID | もし | なら | 理由 | 例外 | 根拠 | 参照 | 状態 |
|---|---|---|---|---|---|---|---|
| 規記-01 | 変更する | 共有ログに書く | 振り返る | — | 〔推論〕 | 型11 | 全状態 |

## しきい値を〔推論〕で置いた規則の一覧

| ID | 値 | なぜその値か（1 行） |
|---|---|---|
| 規換-15 ★ | 20% | 閾07 と同じ |
"""

KATA_90 = """# 付録A 式の一覧（見本）

## §2 式の一覧

| 番号 | 式 | 記号の意味 | 前提と仮定 | 数値例（仮定） | 使う章 | 根拠 | 既存メモ |
|---|---|---|---|---|---|---|---|
| 式 4 | 入札 ≒ 係数 × 予測した価値 | 係数は目標値と予算から決まる | 理論上の整理 | 目標を 20% 下げると入札も下がる | 型01§5 | 〔公式〕＋〔推論〕 | 00§4-1 |

| 番号 | 式 | 前提と仮定 | 数値例 | 使う章 | 根拠 |
|---|---|---|---|---|---|
| 式 7 | 振れ ±1.96 ÷ √n | ポアソン | 30 件で ±36% | 型04§4 | 〔推論〕 |
"""

KATA_91 = """# 付録B しきい値の一覧（見本）

## §1 正本の一覧（閾01〜閾36）

| 番号 | 事柄 | 値 | 根拠の種類 | 出どころ | なぜその値か | 使う章と規則 ID | 変えてよい条件 |
|---|---|---|---|---|---|---|---|
| 閾02 | 評価窓 W | max（2 サイクル, 14 日） | 〔公式〕＋〔推論〕 | 02§4 | 落ち着くまで | 規時-04 | 広げてよい |
| 閾08 | 予算を 1 回に動かす幅 | 20% 以内（減額も同じ） | 〔経験則〕＋〔公式〕（地域実験の文脈だけ） | 26§3.3 | 取るオークションが変わる | 規換-12 | 狭めてよい |
| 閾12 | 予算が効いていると見る線 | 損失率（予算）10% 以上 | 〔推論〕＋〔公式〕 | 02§6 | 5% より緩い | 規換-12 | — |

## §2 集計

### 2-3. 〔推論〕で置いたしきい値の一覧

| 番号 | 値 | 導き方 | 検証できるか |
|---|---|---|---|
| 閾12 | 10% | 公式の 5% より緩い | 増額で確かめる |

## §3 章で追加されたしきい値（正本に番号の無い数値）

| ファイル・場所 | 値 | 根拠の種類 | 意見 |
|---|---|---|---|
| 型04§10、規換-15 | 入札単価の上限を変えたら 7 日見てから次へ | 〔推論〕02§15 | 置いたままでよい |

## §7 知識ベースのルール表（`rules.csv`／`92_ルール表_統合.md`）との対応

| この体系 | ルール表の id | ルール表の印 | 中身（要旨） |
|---|---|---|---|
| 閾02 | `R-入札-157` | ✅ | 2 サイクルか 2 週間 |
| 閾08 | `R-運用-002`、`R-実験-036` | △、✅ | 予算を上げる幅に公式の数字は無い |
"""

KATA_92 = """# 付録C 食い違いと裁定（見本）

## 第 1 部 裁定したもの

### 裁定 1 学習期間（最長 7 日か 7〜14 日か）

| 項目 | 中身 |
|---|---|
| 事柄 | 「学習中」は何日続くか |
| **裁定** | 表示と過渡を分ける |

### 裁定 10 管理画面のメモ

| 項目 | 中身 |
|---|---|
| 事柄 | 注釈の機能 |

## 第 2 部 記録だけしたもの

### 2-11. 追加の裁定（裁定 15〜21）

| 裁定 | 元の行 | 事柄 | 裁定 | 理由（仕組み） |
|---|---|---|---|---|
| 裁定 15 | 7-1 | ブランドの除外 | 無効になる前提 | 安全側 |
"""

KATA_93 = """# 付録D 未確認の点（見本）

## 第 1 部 この体系が依っている未確認の点

| # | 事柄 | 今の扱い | 外れていた場合に変わる章と規則 | 確かめ方 | 優先度 |
|---|---|---|---|---|---|
| D1 | β の値 | 仮置き 0.7 | 型02 | 増額で測る | 高 |
| D3 | 入札単価の上限の変更が「学習中」の引き金か | 記載なし | 規換-15 | 変えて見る | 中 |
"""

KATA_12 = "# 型12 例題（見本）\n\n## 例題 1\n\n予算を増やす前に計測を確かめる。\n"

CORE_90 = """# 90 核となる理解（見本）

- 確認日: 2026-11-17

## 5. 時間の法則

学習期間の「表示」と「過渡」を分ける（`型04§2`、`R-入札-081`）。
"""

RULES_CSV_ROWS = [
    ["R-入札-157", "入札", "全体", "成績は 1〜2 サイクルで落ち着く。評価は 2 サイクルか 2 週間の長いほう。", "2 週間", "該当なし", "", "✅",
     "公式ヘルプ", "https://support.google.com/google-ads/answer/1", "2026-11-17", "02§4", "", ""],
    ["R-運用-002", "運用", "全体", "予算を一度に上げる幅に公式の数字は無い。学習期間の話と混ぜない。", "", "該当なし", "", "△", "公式ヘルプ;実務家",
     "https://support.google.com/google-ads/answer/2", "2026-11-17", "T2 3-3", "A: 20%／B: 記載なし", ""],
]

QUIZ_ROWS = [
    {"id": "BID-04", "分野": "自動入札と予算", "難しさ": "実務", "型": "落とし穴",
     "問い": "「予算を変えると学習がリセットされる」は公式か。", "正解": "公式ではない。予算の変更では学習中にならない（R-入札-081）。",
     "よくある誤答": "予算や目標を大きく変えると学習がリセットされる。", "なぜ誤答するか": "実務家の経験則を公式と読む。",
     "根拠": "['02 §4', 'R-入札-081']", "印": "✅", "採点の要点": "['学習中にならない', '過渡は起きる']"},
    {"id": "ML-01", "分野": "機械学習とオークションの仕組み", "難しさ": "基本", "型": "なぜ",
     "問い": "入札は何で決まるか。", "正解": "係数 × 予測。", "よくある誤答": "入札単価の上限で決まる。",
     "なぜ誤答するか": "上限と入札を混同する。", "根拠": "['00 §4-1']", "印": "✅", "採点の要点": "['係数 × 予測']"},
    {"id": "BID-09", "分野": "自動入札と予算", "難しさ": "難", "型": "未確認",
     "問い": "入札単価の上限の変更は学習中の引き金か。", "正解": "未確認（D3）。", "よくある誤答": "必ず学習中になる。",
     "なぜ誤答するか": "推測で埋める。", "根拠": "['型93 D3']", "印": "❓", "採点の要点": "['未確認と言う']"},
]


@pytest.fixture()
def kbdir(tmp_path, monkeypatch):
    root = tmp_path / "媒体仕様メモ" / "Google広告の基礎"
    (root / "運用の型").mkdir(parents=True)
    (root / "tips").mkdir()
    (root / "ロジックツリー").mkdir()
    files = {
        "00_機械学習の全体像.md": MEMO_00, "12_デマンドジェネレーション.md": MEMO_12,
        "94_裏取りの記録_00-16.md": CHECK_A, "94_裏取りの記録_20-37.md": CHECK_B,
        "tips/T2_入札.md": TIPS, "ロジックツリー/L2_クリック単価.md": TREE,
        "運用の型/00_全体の地図.md": KATA_00, "運用の型/06_アカウントの状態の判定.md": KATA_06,
        "運用の型/08_判断の規則集.md": KATA_08, "運用の型/12_例題.md": KATA_12,
        "運用の型/90_付録A_式の一覧.md": KATA_90, "運用の型/91_付録B_しきい値の一覧.md": KATA_91,
        "運用の型/92_付録C_食い違いと裁定.md": KATA_92, "運用の型/93_付録D_未確認の点.md": KATA_93,
        "92_ルール表_統合.md": "# 92 ルール表（統合）\n\n- 確認日: 2026-11-17\n\n## 1. 入札\n\n学習期間の行は rules.csv と同じ。\n",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    with (root / "rules.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(kb.RULE_COLUMNS)
        w.writerows(RULES_CSV_ROWS)
    (root / "quiz.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in QUIZ_ROWS) + "\n", encoding="utf-8")
    monkeypatch.setenv(kb.ENV_KB_DIR, str(root))
    kb._csv_cache.clear()
    quiz._cache.clear()
    return root


def _bump(p: Path, text: str):
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


# ---------------- 番号の重なり ----------------

def test_番号の重なり_直下は番号_運用の型は型NN(kbdir):
    assert kb.find_doc("12")["rel"] == "12_デマンドジェネレーション.md"
    for f in ("型12", "型012", "運用の型/12", "運用の型/12_例題.md", "12_例題", "例題"):
        assert kb.find_doc(f)["rel"] == "運用の型/12_例題.md", f
    assert kb.find_doc("型08")["rel"] == "運用の型/08_判断の規則集.md"
    assert kb.find_doc("型90")["rel"] == "運用の型/90_付録A_式の一覧.md"
    assert kb.find_doc("00")["rel"] == "00_機械学習の全体像.md" and kb.find_doc("型00")["rel"] == "運用の型/00_全体の地図.md"
    assert kb.find_doc("T2")["kind"] == "Tips" and kb.find_doc("L2")["kind"] == "ロジックツリー"
    with pytest.raises(kb.KBError, match="見つからない"):
        kb.find_doc("型55")


def test_番号だけで決まらないときは候補を返す(kbdir):
    with pytest.raises(kb.KBAmbiguous) as ex:
        kb.find_doc("94")
    assert [c["指定"] for c in ex.value.candidates] == ["94_裏取りの記録_00-16", "94_裏取りの記録_20-37"]
    out = kb.read("94")  # 黙って 1 本目を返さない
    assert "本文" not in out and len(out["候補"]) == 2 and "1 本に決まらない" in out["注記"][0]
    assert kb.read("94_裏取りの記録_20-37")["題名"].startswith("94 裏取りの記録（20〜37）")
    s = kb.search("学習", file="94")
    assert set(s["件数"]["ファイルごとの当たり（上位）"]) <= {"94_裏取りの記録_00-16.md", "94_裏取りの記録_20-37.md"}
    assert any("2 本に当たった" in n for n in s["注記"])
    assert "2 本ある" in kb.cite("94", "x")["確かめ"]  # 根拠の行の引き直しも同じ決まりで、落ちずに知らせる
    # 一覧の「番号」は、そのまま kb_read に渡せる名前
    rows = {r["ファイル"]: r["番号"] for r in kb.overview()["行"]}
    assert rows["運用の型/12_例題.md"] == "型12" and rows["12_デマンドジェネレーション.md"] == "12"
    assert rows["94_裏取りの記録_00-16.md"] == "94_裏取りの記録_00-16"


def test_根拠の行の引き直しは直下の12を引く(kbdir):
    ok = kb.cite("12", "画像", section="2.", expect=("1.91",))
    assert ok["ファイル"] == "12_デマンドジェネレーション.md" and ok["確かめ"] == "数字は知識ベースの行と合う"
    assert kb.cite_line("型12", "計測を確かめる")["ファイル"] == "運用の型/12_例題.md"


# ---------------- 一覧・探す・ルール ----------------

def test_一覧_最初に読むもの_と種類(kbdir):
    out = kb.overview()
    assert list(out)[:4] == ["対象アカウント", "期間", "単位", "最初に読むもの"]
    assert [f["ファイル"] for f in out["最初に読むもの"]] == ["運用の型/00_全体の地図.md"]  # 90 はまだ無い
    assert any("90_核となる理解.md はまだ無い" in n for n in out["注記"])
    kinds = out["件数"]["種類ごと"]
    assert kinds["運用の型（体系）"] == 8 and kinds["裏取りの記録"] == 2 and kinds["ルール統合表"] == 1
    assert out["件数"]["機械用の表"] == {"rules.csv": 2, "quiz.jsonl": 3}
    (kbdir / "90_核となる理解.md").write_text(CORE_90, encoding="utf-8")
    out = kb.overview()
    assert [f["読むには"] for f in out["最初に読むもの"]] == ["kb_read(file='90')", "kb_read(file='型00')"]
    assert out["件数"]["種類ごと"]["核となる理解"] == 1
    assert kb.overview("体系")["件数"]["ファイル"] == 8 and kb.overview("裏取り")["件数"]["ファイル"] == 2


def test_探す_並びは90_運用の型_統合表_知識メモ_ツリー_Tips(kbdir):
    (kbdir / "90_核となる理解.md").write_text(CORE_90, encoding="utf-8")
    out = kb.search("学習期間", max_results=50)
    kinds = [r["種類"] for r in out["行"] if r["形"] != "見出し"]
    order = ["核となる理解", "運用の型（体系）", "ルール統合表", "知識メモ", "ロジックツリー", "Tips"]
    seen = [k for k in dict.fromkeys(kinds) if k in order]
    assert seen == [k for k in order if k in seen] and seen[0] == "核となる理解"
    heads = [r for r in kb.search("学習期間 7")["行"]]
    assert heads[0]["形"] == "見出し"  # 見出しの一致が先、は今までどおり
    assert all(r["種類"] == "運用の型（体系）" for r in kb.search("予算", kind="運用の型（体系）")["行"])


def test_ルール_運用の型と90も引く(kbdir):
    (kbdir / "90_核となる理解.md").write_text(CORE_90, encoding="utf-8")
    out = kb.rules("学習期間")
    assert [r["id"] for r in out["行"]] == ["R-運用-002"]
    assert out["核となる理解（90）"][0]["ファイル"] == "90_核となる理解.md"
    k = kb.rules("予算 20%")
    assert {r["ID"] for r in k["運用の型の規則（型08。いつ・何をするか）"]} >= {"規時-04", "規換-12"}
    only = kb.rules("規換-15", kind="体系")
    assert only["行"][0]["ID"] == "規換-15"
    assert kb.rules("", file="型08", max_results=50)["件数"]["当たり"] == 6
    core = kb.rules("過渡", kind="核となる理解")
    assert core["行"] and core["行"][0]["種類"] == "核となる理解"
    with pytest.raises(kb.KBError, match="kind"):
        kb.rules("a", kind="Tips")


def test_90が無くても動く(kbdir):
    assert kb.overview()["最初に読むもの"]
    assert "注記" in kb.rules("過渡", kind="核となる理解")
    assert "核となる理解（90）" not in kb.rules("学習期間")
    assert kb.check_claim("予算を変えると学習がリセットされる")["件数"]["核となる理解・運用の型（体系）"] >= 0


# ---------------- kb_kata ----------------

def test_規則を番号で引くと全文の行としきい値と式の中身(kbdir):
    out = kata.kata("規時-04")
    r = out["行"][0]
    assert r["ID"] == "規時-04" and r["条件"].startswith("目標値または予算") and r["行動"].startswith("変更日から W")
    assert r["理由"] and r["例外"] == "—" and r["参照"] == "型04§10" and r["使う状態"] == "状8"
    assert r["根拠の種類の一覧"] == ["推論", "経験則"] and r["分類"].startswith("規時")
    assert r["指すしきい値"] == ["閾02"] and r["指す式"] == ["式 4"]
    assert out["しきい値の中身（型91）"]["閾02"]["値"].startswith("max")
    assert out["しきい値の中身（型91）"]["閾02"]["rules.csv の id"][0]["id"] == "R-入札-157"
    assert out["式の中身（型90）"]["式 4"]["式"].startswith("入札")
    assert r["ファイル"] == "運用の型/08_判断の規則集.md" and r["行"] > 0
    assert kata.kata("規時-9")["行"][0]["指す式"] == ["式 6", "式 7"]  # 0 を省いても、式 6・7 の書き方でも
    assert "見つからない番号" in kata.kata("規換-99")["注記"][0]


def test_規則を分類と状態と語で引く(kbdir):
    assert [r["ID"] for r in kata.kata("規換")["行"]] == ["規換-12", "規換-15"]
    st = kata.kata(state="状4")
    assert [r["ID"] for r in st["行"]] == ["規時-09", "規換-12", "規換-15"]  # 優先の順位（規守 1 → 規時 4 → 規換 8）
    assert st["状態の使い方（型08 の使い方の表）"][0]["触ってはいけないもの"] == "特定した制約以外のつまみ"
    assert any("全状態" in n for n in st["注記"])
    assert [r["ID"] for r in kata.kata("状0")["行"]] == ["規守-01", "規換-15"]
    assert [r["ID"] for r in kata.kata("上限 7 日")["行"]] == ["規換-15"]  # 空白区切りは AND
    assert [r["ID"] for r in kata.kata("規換 状4 予算")["行"]] == ["規換-12"]
    # 〔推論〕の一覧の表（規換-15 ★）は規則として数えない
    assert kata.kata("規換-15")["件数"]["当たり"] == 1 and kata.kata()["分類（型08）"][0]["接頭辞"] == "規守"
    with pytest.raises(kb.KBError, match="状0〜状8"):
        kata.kata(state="詰まり")


def test_しきい値_根拠の種類とrules_csvのidと推論の注意(kbdir):
    t = kata.kata("閾08")["行"][0]
    assert t["値"] == "20% 以内（減額も同じ）" and t["根拠の種類の一覧"] == ["経験則", "公式"]
    assert [x["id"] for x in t["rules.csv の対応"]] == ["R-運用-002", "R-実験-036"]
    assert t["rules.csv の対応"][0]["印（rules.csv）"] == "△" and t["rules.csv の対応"][1]["印（rules.csv）"] == "（rules.csv に無い）"
    assert "注意" not in t
    t12 = kata.kata("閾12")
    assert t12["件数"]["当たり"] == 1  # §2-3 の重複の行は数えない
    assert "公式の数字ではない" in t12["行"][0]["注意"] and "対応表に" in t12["行"][0]["rules.csv の対応"]
    w = kata.kata("入札単価の上限", kind="しきい値")["行"]
    assert w[0]["番号"] is None and "番号の無いしきい値" in w[0]["扱い"] and "公式の数字ではない" in w[0]["注意"]
    assert len(kata.kata(kind="しきい値")["行"]) == 3


def test_式を引く(kbdir):
    assert kata.kata("式 4")["行"][0]["前提と仮定"] == "理論上の整理"
    assert kata.kata("式7")["行"][0]["数値例"] == "30 件で ±36%"
    assert [f["番号"] for f in kata.kata("ポアソン", kind="式")["行"]] == ["式 7"]


def test_状態の判定は問いと枝を返し判定しない(kbdir):
    g = kata.kata(kind="状態")
    assert [q["問"] for q in g["問い"]] == ["問 0", "問 6"]
    assert g["問い"][1]["行き先"] == ["状4", "状7"] and "90% 未満" in g["問い"][1]["枝"][0]
    assert "判定しない" in g["この道具がしないこと"] and "```" not in g["決定木（そのまま）"]
    assert [c["問"] for c in g["判定の基準の節（型06§3）"]] == ["問 0", "問 6"]
    assert {r["状態"] for r in g["状態ごとの使い方（型08）"]} == {"状0", "状4"}
    s4 = kata.kata(kind="状態", state="状4")
    assert [q["問"] for q in s4["この状態に行き着く問い"]] == ["問 6"]
    assert s4["状態ごとの打ち手（型06§5）"][0]["最初にすること"] == "制約を 1 つに特定する"
    assert s4["状態ごとの使い方（型08）"][0]["主に引く分類"].startswith("制約を 1 つに")


def test_裁定と未確認(kbdir):
    r1 = kata.kata("裁定 1")["行"]
    assert len(r1) == 1 and r1[0]["見出し"].startswith("裁定 1 学習期間") and "表示と過渡を分ける" in r1[0]["本文"]
    assert kata.kata("裁定 15")["行"][0]["行の中身"]["事柄"] == "ブランドの除外"  # 表の行の裁定
    assert kata.kata("学習中", kind="裁定")["件数"]["当たり"] >= 1
    d = kata.kata("D3", kind="未確認")
    assert d["行"][0]["行の中身"]["事柄"].startswith("入札単価の上限") and "未確認" in d["注記"][0]
    assert kata.kata("β", kind="未確認")["行"][0]["行の中身"]["#"] == "D1"
    with pytest.raises(kb.KBError, match="kind"):
        kata.kata("x", kind="ブログ")


def test_行が増えたり変更の記録の節が付いても引ける(kbdir):
    p = kbdir / "運用の型" / "08_判断の規則集.md"
    text = p.read_text(encoding="utf-8").replace(
        "| 規換-15 |", "| 規換-13 | 新しい条件 | 新しい行動 | 理由 | — | 〔推論〕 | 型06 | 状4 |\n| 規換-15 |")
    text += "\n## 変更の記録\n\n| 日付 | 規則 | 変えたこと |\n|---|---|---|\n| 2026-11-18 | 規換-15 | 7 日に直した |\n"
    _bump(p, text)
    assert [r["ID"] for r in kata.kata("規換")["行"]] == ["規換-12", "規換-13", "規換-15"]
    assert kata.kata("規換-15")["件数"]["当たり"] == 1


# ---------------- kb_quiz ----------------

def test_quiz_既定では正解を返さない(kbdir):
    out = quiz.quiz(field="入札", count=2)
    assert out["件数"]["当たり"] == 2 and len(out["行"]) == 2
    for r in out["行"]:
        assert set(r) == {"id", "分野", "型", "難しさ", "問い", "ファイル", "行"}
    text = json.dumps(out, ensure_ascii=False)
    assert "公式ではない" not in text and "よくある誤答" not in json.dumps(out["行"], ensure_ascii=False)
    assert [r["id"] for r in quiz.quiz(difficulty="難")["行"]] == ["BID-09"]
    assert [r["id"] for r in quiz.quiz(mark="❓")["行"]] == ["BID-09"]
    assert [r["id"] for r in quiz.quiz(type="なぜ")["行"]] == ["ML-01"]
    assert [r["id"] for r in quiz.quiz("学習 リセット")["行"]] == ["BID-04"]
    assert len(quiz.quiz(shuffle=True, seed=1, count=3)["行"]) == 3


def test_quiz_答え合わせと誤答(kbdir):
    a = quiz.quiz(id="bid-04")["行"][0]
    assert a["正解"].startswith("公式ではない") and a["根拠"] == ["02 §4", "R-入札-081"] and a["採点の要点"][0] == "学習中にならない"
    assert a["よくある誤答"] and a["なぜ誤答するか"] and a["印"] == "✅"
    two = quiz.quiz(id="BID-04, ML-01", mode="答え")
    assert [r["id"] for r in two["行"]] == ["BID-04", "ML-01"]
    assert "見つからない" in quiz.quiz(id="X-1")["注記"][0]
    m = quiz.quiz("予算を変えると学習がリセットされる", mode="誤答")
    assert m["行"][0]["id"] == "BID-04" and m["行"][0]["正解"].startswith("公式ではない")
    assert quiz.quiz("ディスプレイの画像は横長", mode="誤答")["件数"]["当たり"] == 0
    with pytest.raises(kb.KBError, match="mode"):
        quiz.quiz(mode="採点")


def test_主張の点検_よくある誤答に近いと先頭で知らせる(kbdir):
    c = kb.check_claim("予算を変えると学習がリセットされる")
    assert list(c)[3] == "よくある誤答に近い"
    assert c["よくある誤答に近い"]["行"][0]["id"] == "BID-04" and c["よくある誤答に近い"]["行"][0]["根拠"]
    assert "よくある誤答に近い" not in kb.check_claim("デマンド ジェネレーションの画像は横長")


def test_quizが無くても動く(kbdir):
    (kbdir / "quiz.jsonl").unlink()
    quiz._cache.clear()
    assert kb.overview()["件数"]["機械用の表"]["quiz.jsonl"] == "無い"
    assert "よくある誤答に近い" not in kb.check_claim("予算を変えると学習がリセットされる")
    with pytest.raises(kb.KBError, match="quiz.jsonl"):
        quiz.quiz()


def test_書き換えない(kbdir):
    before = {p: p.read_bytes() for p in kbdir.rglob("*") if p.is_file()}
    kb.overview()
    kata.kata("規換-15")
    kata.kata(kind="状態", state="状4")
    kata.kata("閾08")
    quiz.quiz(id="BID-04")
    kb.check_claim("予算を変えると学習がリセットされる")
    assert before == {p: p.read_bytes() for p in kbdir.rglob("*") if p.is_file()}


# ---------------- 実物の知識ベース（あるときだけ） ----------------

REAL = Path(kb.DEFAULT_KB_DIR)
real = pytest.mark.skipif(not (REAL / "運用の型").is_dir(), reason="実物の運用の型が無い環境")


@real
def test_実物_番号の重なりと型NN(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    assert kb.find_doc("12")["rel"].startswith("12_デマンド") and kb.find_doc("型12")["rel"] == "運用の型/12_例題.md"
    assert kb.find_doc("型08")["rel"].startswith("運用の型/08_") and kb.find_doc("型91")["rel"].startswith("運用の型/91_")
    assert len(kb.read("94")["候補"]) >= 2
    out = kb.overview()
    assert any(f["ファイル"] == "運用の型/00_全体の地図.md" for f in out["最初に読むもの"])
    assert {"運用の型（体系）", "ルール統合表", "理解度テスト", "裏取りの記録"} <= set(out["件数"]["種類ごと"])


@real
def test_実物_運用の型を引ける(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    r = kata.kata("規換-15")["行"][0]
    assert r["ID"] == "規換-15" and r["条件"] and r["行動"] and r["根拠の種類"] and r["使う状態"]
    rules = kata.all_rules(kb.find_doc("型08"))
    assert len(rules) > 100 and len({kata._rule_id(kata._first(u)) for u in rules}) == len(rules)  # 番号の重複なし
    s4 = kata.kata(state="状4", max_results=50, max_chars=30000)
    assert s4["行"] and all("状4" in x["使う状態"] for x in s4["行"]) and s4["状態の使い方（型08 の使い方の表）"]
    t = kata.kata("閾08")["行"][0]
    assert t["根拠の種類"] and isinstance(t["rules.csv の対応"], list) and t["rules.csv の対応"][0]["id"].startswith("R-")
    assert all("注意" in x for x in kata.kata(kind="しきい値", max_results=50, max_chars=30000)["行"] if "推論" in x["根拠の種類"])
    assert kata.kata("式 4")["行"][0]["番号"] == "式 4"
    g = kata.kata(kind="状態")
    assert len(g["問い"]) >= 5 and g["状態ごとの使い方（型08）"]
    assert kata.kata("裁定 1")["行"][0]["形"] == "節"


@real
def test_実物_quiz(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    q = quiz.quiz_rows()
    if q is None:
        pytest.skip("quiz.jsonl が無い")
    assert len(q["rows"]) > 100 and not q["bad_lines"]
    assert len({r["id"] for r in q["rows"]}) == len(q["rows"])
    assert all(set(quiz.QUIZ_KEYS) <= set(r) for r in q["rows"])
    out = quiz.quiz(field="入札", count=3)
    assert len(out["行"]) == 3 and all("正解" not in r for r in out["行"])
    assert quiz.quiz(id=out["行"][0]["id"])["行"][0]["正解"]
    c = kb.check_claim("予算を変えると学習がリセットされる")
    assert c.get("よくある誤答に近い")
