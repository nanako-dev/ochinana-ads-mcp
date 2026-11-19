"""週次の運用レポート（自分用）の材料と骨組み（report_weekly_self）。本物の API は呼ばない。"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import config, decisions, numcheck, templates, tools

TODAY = date(2026, 11, 19)
JST = timezone(timedelta(hours=9))
CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))


def E(name):
    return NS(name=name)


def api_row(cid, name, d, imp, clicks, yen, conv):
    return NS(campaign=NS(id=cid, name=name, status=E("ENABLED"), advertising_channel_type=E("SEARCH")),
              campaign_budget=NS(amount_micros=2000 * 1_000_000), segments=NS(date=d.isoformat()),
              metrics=NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, conversions=conv,
                         conversions_value=0.0))


ROWS = [api_row(1, "A", date(2026, 10, 15) + timedelta(days=i), 1000, 50, 1500 + 10 * (i % 3), 2) for i in range(35)]


def fake_run(seen):
    def run(customer_id, query):
        assert query.startswith("SELECT ")
        seen.append(query)
        resource = query.split(" FROM ")[1].split()[0]
        if resource == "customer":
            return [CUSTOMER]
        if resource != "campaign":
            return []
        if "BETWEEN '" not in query:
            return ROWS[:1]
        lo = query.split("BETWEEN '")[1][:10]
        hi = query.split("AND '")[1][:10]
        return [r for r in ROWS if lo <= r.segments.date <= hi]
    return run


CHECKS = {"まとめ": {"所見": {"大": 0, "中": 2, "小": 0}, "判定できない": 1, "問題なし": 5},
          "所見": [
              {"点検": "C10", "対象": "A（予算）", "重さ": "中", "所見": "表示の機会の 59.6% を予算で失っている。",
               "根拠": {"予算で失った割合（%）": 59.6},
               "直し方の案": [{"順": 1, "種類": "品質", "案": "クリック単価を下げる側の打ち手を先にやる。"},
                          {"順": 2, "種類": "入札・予算", "案": "それでも不足なら: 予算を上げる（必ず運用者の承認を取る）"}]},
              {"点検": "C06", "対象": "A", "重さ": "中", "所見": "クリックが無い検索語句が 3 個。",
               "根拠": {"検索語句の数": 3},
               "直し方の案": [{"順": 1, "種類": "品質", "案": "合わない語を除外語句にする案を作る。"}]}],
          "判定できなかったもの": [{"点検": "C07"}]}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path / "out"))
    monkeypatch.setenv(decisions.ENV_LOG_DIR, str(tmp_path / "log"))
    monkeypatch.setattr(tools, "run_checks", lambda *a, **k: CHECKS)
    yield
    tools._accounts.clear()


def make(**kw):
    seen = []
    return tools.report_weekly_self("1112223333", run=fake_run(seen), today=TODAY, **kw), seen


def test_許可していないアカウントはAPIの前に断る():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    with pytest.raises(ValueError, match="許可リスト"):
        tools.report_weekly_self("1234567890", run=run, today=TODAY)


def test_型の節とネクストアクションの決まり():
    t = templates.WEEKLY_SELF
    names = [x["名前"] for x in t["節"]]
    assert names[1] == "前回決めたこと → その結果" and names[7] == "ネクストアクション" and len(names) == 9
    rules = " ".join(t["ネクストアクションの決まり"])
    assert "入札・予算" in rules and "それでも不足なら" in rules and "約 2 週間" in rules and "10 件" in rules


def test_骨組みは昨日までの7日で_表と節が入り_ファイルに残る():
    out, seen = make(target_cpa_yen=800)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["期間"].startswith("今: 2026-11-12〜2026-11-18（7 日）／前: 2026-11-05〜2026-11-11")
    md = out["骨組み"]
    for h in ("## 1. いちばん大事なこと", "## 2. 前回決めたこと → その結果", "## 8. ネクストアクション", "## 9. 確かめていないこと・暫定"):
        assert h in md
    assert "| 指標 | 今（11/12〜11/18）" in md and "目標 800 円 に対して" in md
    assert Path(out["骨組みの保存先"]).read_text(encoding="utf-8") == md
    assert all(q.startswith("SELECT ") for q in seen) and not any("user_email" in q for q in seen)


def test_骨組みの数字は全部_束と突き合う():
    decisions.log_decision("1112223333", "見出しを 3 本足す", "C02 の所見", "運用者の承認",
                           now=datetime(2026, 11, 3, 10, 0, tzinfo=JST))
    out, _ = make(target_cpa_yen=800, plan_cost_yen=60000, plan_start="2026-11-01", plan_end="2026-11-30")
    r = tools.report_number_check(out["骨組み"], out["束ID"])
    assert r["結果"] == "全部一致", r["直すもの"]
    assert out["材料の数"]["期間の判断の記録"] == 1 and out["材料の数"]["束に足した数字"] > 0


def test_前回の判断は前後の差と評価の時期が付く():
    decisions.log_decision("1112223333", "見出しを 3 本足す", "C02 の所見", "運用者の承認",
                           now=datetime(2026, 11, 3, 10, 0, tzinfo=JST))
    decisions.log_decision("1112223333", "除外語句を 2 つ足す", "C06", "AI の提案",
                           now=datetime(2026, 11, 15, 10, 0, tzinfo=JST))
    decisions.log_decision("4445556666", "別のアカウント", "x", "AI の提案", now=datetime(2026, 11, 15, 10, 0, tzinfo=JST))
    out, _ = make()
    md = out["骨組み"]
    assert "2026-11-03 見出しを 3 本足す（運用者の承認）。評価の時期: 来ている" in md
    assert "2026-11-15 除外語句を 2 つ足す（AI の提案）。評価の時期: まだ早い（2026-11-29 以降）" in md
    assert "別のアカウント" not in md
    assert "  - 費用（円）: " in md


def test_ネクストアクションは段の順で_入札予算は最後_評価待ちは様子を見る():
    decisions.log_decision("1112223333", "除外語句を 2 つ足す", "C06", "AI の提案",
                           now=datetime(2026, 11, 15, 10, 0, tzinfo=JST))
    out, _ = make()
    c = out["次にやることの候補"]
    stages = [x["段"] for x in c]
    assert stages == sorted(stages, key=templates.stage_rank)
    assert stages[0] == "絞る" and "入札・予算" in stages and stages[-1] == "様子を見る"
    bid = next(x for x in c if x["段"] == "入札・予算")
    assert bid["案"].startswith("それでも不足なら") and bid["案"].count("それでも不足なら") == 1
    assert bid["承認"].startswith("要る")
    assert all(x["誰の案"] in ("AI の提案（未承認）", "道具（時間の法則）") for x in c)


def test_読めない材料は書いて先へ進む(monkeypatch):
    def boom(*a, **k):
        raise ValueError("テストのための失敗")

    monkeypatch.setattr(tools, "get_change_history", boom)
    out, _ = make()
    assert out["読めなかった材料"] == ["変更の履歴（get_change_history）: テストのための失敗"]
    assert "読めなかった材料: 変更の履歴" in out["骨組み"]


def test_突き合わせは書く欄と時刻と引数を見ない():
    nums = [{"項目": "合計の費用（今）", "値": 1000, "単位": "円"}]
    text = "【書く: 三行まで。材料は 6 の所見】\n2026-11-18 13:56:47.867631 作成。lag_days=7 の仮置き。費用は 1,000 円。1 日の予算の話。"
    r = numcheck.check(text, nums)
    assert r["結果"] == "全部一致", r["直すもの"]


def test_固定中のキャンペーンを変える案は様子を見るに回す(monkeypatch):
    monkeypatch.setattr(config, "FROZEN_CAMPAIGNS", {"A": ("2026-12-18", "検証中")})
    out, _ = make()
    c = out["次にやることの候補"]
    assert all(x["段"] == "様子を見る" for x in c if "点検" in x["出どころ"])
    assert all(x["案"].startswith("（2026-12-18 まで固定中") for x in c if "点検" in x["出どころ"])
    assert "- 固定中: A（2026-12-18 まで。検証中）" in out["骨組み"]
    r = tools.report_number_check(out["骨組み"], out["束ID"])
    assert r["結果"] == "全部一致", r["直すもの"]
    # 期限を過ぎたら効かない
    out2 = tools.report_weekly_self("1112223333", run=fake_run([]), today=date(2026, 12, 19))
    assert not any(x["段"] == "様子を見る" for x in out2["次にやることの候補"] if "点検" in x["出どころ"])


def test_突き合わせ_間に別の数字があれば前の指標の名前は使わない():
    nums = [{"項目": "合計のコンバージョン単価（今）", "値": 6295, "単位": "円"},
            {"項目": "合計のコンバージョン（今）", "値": 2.0, "単位": "件"},
            {"項目": "評価の目安: 成果の件数", "値": 10, "単位": "件"}]
    r = numcheck.check("CPA 6,295 円は、2.0 件しかない。成果が 10 件を超えるまで待つ。", nums)
    assert r["結果"] == "全部一致", r["直すもの"]
    r = numcheck.check("CPA は 2.0 件。", nums)
    assert r["直すもの"][0]["結果"] == "指標が違う疑い"
