"""統合したルール表（rules.csv）を kb_rules が先に引くことを確かめる。"""
import csv
import os
from pathlib import Path

import pytest

from ochinana_ads import kb

COLS = list(kb.RULE_COLUMNS)
ROWS = [
    ["R-入札-001", "入札", "全体", "目標コンバージョン単価の入札単価の上限・下限はポートフォリオだけで設定できる。", "", "条件つき", "ディスプレイ専用では不可",
     "✅", "公式ヘルプ", "https://support.google.com/google-ads/answer/6268632", "2026-11-16", "14#8", "", ""],
    ["R-入札-002", "入札", "検索", "コンバージョン数の最大化のポートフォリオの上限クリック単価が効くかは公式の中で食い違う。", "", "条件つき", "",
     "△", "API の仕様;公式ヘルプ", "https://developers.google.com/google-ads/api/reference/rpc/latest/MaximizeConversions", "2026-11-16", "14#11",
     "A: API は検索では無視する／B: ヘルプは安全策として付けられる", ""],
    ["R-入札-003", "入札", "全体", "目標コンバージョン単価の学習期間は 7〜14 日（公式に記載なし）。", "7〜14 日", "該当なし", "", "❓", "推測",
     "https://support.google.com/google-ads/answer/6268632", "2026-11-17", "15:4-41", "", ""],
    ["R-上限-001", "アカウントの上限", "動画", "フリークエンシー目標設定のキャンペーンに作れる広告グループは 1 つだけ。", "1 つ", "できない", "", "✅",
     "公式ヘルプ", "https://support.google.com/google-ads/answer/12400225", "2026-11-16", "11§10-26", "", ""],
    ["R-審査-001", "ポリシーと審査", "全体", "6 か月以上前のポリシーの決定は管理画面から再審査請求できない。", "6 か月", "できない", "", "✅", "公式ヘルプ",
     "https://support.google.com/google-ads/answer/9338593", "2026-11-16", "15:7-13", "", "2026-07-21: 管理画面からの請求を停止"],
]


@pytest.fixture
def kbdir(tmp_path, monkeypatch):
    root = tmp_path / "Google広告の基礎"
    root.mkdir()
    (root / "15_細かいルール表.md").write_text(
        "# 15（見本）\n\n- 確認日: 2026-11-16\n\n## 1. 仕様\n\n| 番号 | 対象 | ルール | 印 | 出典 |\n|---|---|---|---|---|\n"
        "| 1-01 | RSA 見出し | 最少 3 本、最大 15 本 | ✅ | H:7684791 |\n", encoding="utf-8")
    with (root / "rules.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(ROWS)
    monkeypatch.setenv(kb.ENV_KB_DIR, str(root))
    kb._csv_cache.clear()
    return root


def test_統合表があれば先に引く(kbdir):
    out = kb.rules("広告グループ")
    assert "rules.csv" in out["単位"]
    assert [r["id"] for r in out["行"]] == ["R-上限-001"]
    r = out["行"][0]
    assert r["値"] == "1 つ" and r["できる／できない"] == "できない" and r["元の場所"] == "11§10-26" and r["ファイル"] == "rules.csv"


def test_分野と対象と印で絞る(kbdir):
    assert [r["id"] for r in kb.rules(field="入札")["行"]] == ["R-入札-001", "R-入札-002", "R-入札-003"]
    assert [r["id"] for r in kb.rules(field="入札", target="検索")["行"]] == ["R-入札-002"]
    assert [r["id"] for r in kb.rules(mark="❓")["行"]] == ["R-入札-003"]
    assert [r["id"] for r in kb.rules(target="動画", field="上限")["行"]] == ["R-上限-001"]


def test_食い違いと変更予定は必ず返す(kbdir):
    tri = kb.rules(mark="△")["行"]
    assert len(tri) == 1 and tri[0]["食い違いの内容"].startswith("A: API は検索では無視する")
    plan = kb.rules("再審査請求")["行"][0]
    assert plan["変更予定"] == "2026-07-21: 管理画面からの請求を停止"
    assert "食い違いの内容" not in kb.rules(mark="✅", field="入札")["行"][0]


def test_元の番号と新しい_id_で引ける(kbdir):
    assert kb.rules("15:4-41")["行"][0]["id"] == "R-入札-003"
    assert kb.rules("r-入札-002")["行"][0]["印"] == "△"


def test_file_を指定すると各メモの表から引く(kbdir):
    out = kb.rules(target="RSA", file="15")
    assert out["行"][0]["番号"] == "1-01"
    with pytest.raises(kb.KBError, match="分野"):
        kb.rules(field="入札", file="15")


def test_統合表が無ければ今までどおり(kbdir):
    (kbdir / "rules.csv").unlink()
    out = kb.rules(target="RSA")
    assert out["行"][0]["番号"] == "1-01" and "rules.csv" not in out["単位"]


def test_統合表が直されたら読み直す(kbdir):
    assert kb.rules("広告グループ")["行"][0]["値"] == "1 つ"
    p = kbdir / "rules.csv"
    p.write_text(p.read_text(encoding="utf-8").replace(",1 つ,", ",2 つ,"), encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert kb.rules("広告グループ")["行"][0]["値"] == "2 つ"


def test_列が足りない統合表は知らせる(kbdir):
    (kbdir / "rules.csv").write_text("id,ルール\nR-1,x\n", encoding="utf-8")
    with pytest.raises(kb.KBError, match="列が足りない"):
        kb.rules()


# ---- 実物の統合表（あるときだけ） ----

REAL = Path(kb.DEFAULT_KB_DIR) / kb.RULES_CSV
real = pytest.mark.skipif(not REAL.is_file(), reason="実物の統合表が無い環境")


@real
def test_実物_利用者の問いで引ける(monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    rows = kb.rule_csv_rows()
    assert len(rows) > 1000 and len({r["id"] for r in rows}) == len(rows)
    assert all(r["印"] in kb.MARKS for r in rows)
    assert all(r["食い違いの内容"] for r in rows if r["印"] == "△")
    # フリークエンシー目標設定の広告グループ数
    assert any(r["値"] == "1 つ" for r in kb.rules("フリークエンシー目標設定 広告グループ", field="上限")["行"])
    # ポートフォリオのコンバージョン数の最大化の上限クリック単価は △、目標コンバージョン単価は ✅
    cap = kb.rules("14#11")["行"][0]
    assert cap["印"] == "△" and cap["食い違いの内容"]
    assert kb.rules("14#8")["行"][0]["印"] == "✅"
    # AI Max の検索語句とのマッチングは個別クリック単価で動かない
    assert kb.rules("個別クリック単価 検索語句とのマッチング")["件数"]["当たり"] >= 1
    # 目標コンバージョン単価の学習 7〜14 日は ❓
    assert kb.rules("15:4-41")["行"][0]["印"] == "❓"
    # DG の日予算の倍率は △
    assert any(r["印"] == "△" for r in kb.rules("目標 CPA 倍", target="デマンド ジェネレーション", field="予算")["行"])
    # 文字数の書き方は「半角 N（全角 N/2）」にそろっている
    assert kb.rules("レスポンシブ検索広告 見出し 半角 30（全角 15）")["件数"]["当たり"] >= 1
