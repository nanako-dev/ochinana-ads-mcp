"""段 3.6 の道具（変更の履歴・変更の前後・いつもの水準からの外れ）を、にせの API の行で確かめる。本物の API は呼ばない。"""
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import config, gaql, monitor, tools

TODAY = date(2026, 11, 19)
CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))


def E(name):
    return NS(name=name)


def fake_run(tables, seen=None):
    def run(customer_id, query):
        assert query.startswith("SELECT ")
        if seen is not None:
            seen.append(query)
        resource = query.split(" FROM ")[1].split()[0]
        return tables.get(resource, [])

    return run


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    yield
    tools._accounts.clear()


def day_row(cid, name, d, imp, clicks, yen, conv=0.0, status="ENABLED", budget_yen=1000):
    return NS(campaign=NS(id=cid, name=name, status=E(status), advertising_channel_type=E("SEARCH")),
              campaign_budget=NS(amount_micros=budget_yen * 1_000_000), segments=NS(date=d.isoformat()),
              metrics=NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, conversions=conv, conversions_value=0.0))


def days(start, n):
    return [start + timedelta(days=i) for i in range(n)]


# ---------------- 許可リスト ----------------

def test_許可していないアカウントはAPIを呼ぶ前に断る():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    for fn, args in [
        (tools.get_change_history, ("1234567890",)),
        (tools.measure_effect, ("1234567890", "2026-11-01")),
        (tools.get_anomalies, ("1234567890",)),
    ]:
        with pytest.raises(ValueError, match="許可リスト"):
            fn(*args, run=run, today=TODAY)


# ---------------- 変更の履歴 ----------------

def change(time, rtype, op, client, paths, old, new, campaign="customers/1112223333/campaigns/1"):
    key = rtype.lower()
    return NS(change_event=NS(
        change_date_time=time, change_resource_type=E(rtype), resource_change_operation=E(op), client_type=E(client),
        change_resource_name=f"customers/1112223333/{key}/9", changed_fields=NS(paths=paths),
        old_resource=NS(**{key: NS(**old)}), new_resource=NS(**{key: NS(**new)}), campaign=campaign, ad_group=""))


def test_変更の履歴は30日より前を断り_LIMITを付けメールを読まない():
    run = fake_run({"customer": [CUSTOMER]})
    with pytest.raises(ValueError, match="過去 30 日"):
        tools.get_change_history("1112223333", "2026-10-20", "2026-11-18", run=run, today=TODAY)
    with pytest.raises(ValueError, match="今日"):
        tools.get_change_history("1112223333", "2026-11-10", "2026-11-20", run=run, today=TODAY)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.get_change_history("1112223333", "11/10", run=run, today=TODAY)
    with pytest.raises(ValueError, match="LIMIT"):
        gaql.change_events_query("2026-11-01", "2026-11-02", 10_001)
    seen = []
    out = tools.get_change_history("1112223333", run=fake_run({"customer": [CUSTOMER]}, seen), today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert "2026-10-21〜2026-11-19" in out["期間"]  # 省くと今日を含む過去 30 日
    q = [x for x in seen if "change_event" in x][0]
    assert "LIMIT 1000" in q and "ORDER BY change_event.change_date_time DESC" in q
    assert "user_email" not in q
    assert "change_event.change_date_time <= '2026-11-20'" in q  # 終了日の分を取りこぼさない
    assert out["件数"]["変更"] == 0 and out["全件の保存先"] is None


def test_変更の履歴は前後の値を円にしキャンペーン名と経路を付ける():
    rows = {
        "customer": [CUSTOMER],
        "change_event": [
            change("2026-11-18 10:00:00.000000", "CAMPAIGN_BUDGET", "UPDATE", "GOOGLE_ADS_WEB_CLIENT",
                   ["amount_micros"], {"amount_micros": 1_500_000_000}, {"amount_micros": 2_000_000_000}),
            change("2026-11-17 08:00:00.000000", "CAMPAIGN", "UPDATE", "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION",
                   ["status"], {"status": "PAUSED"}, {"status": "ENABLED"}),
            change("2026-11-16 08:00:00.000000", "AD_GROUP_CRITERION", "CREATE", "GOOGLE_ADS_AUTOMATED_RULE",
                   ["keyword.text", "cpc_bid_micros"], {}, {"keyword": NS(text="ホッケー 観戦"), "cpc_bid_micros": 80_000_000}),
            change("2026-11-20 00:00:00.000000", "CAMPAIGN", "UPDATE", "GOOGLE_ADS_WEB_CLIENT", ["name"],
                   {"name": "a"}, {"name": "b"}),  # 終了日の翌日の 0 時ちょうど（問い合わせの端）は省く
        ],
        "campaign": [NS(campaign=NS(id=1, name="検索A", status=E("ENABLED")))],
    }
    out = tools.get_change_history("1112223333", "2026-11-10", "2026-11-19", run=fake_run(rows), today=TODAY)
    assert out["件数"]["変更"] == 3
    first = out["行"][0]
    assert first["種類"] == "予算" and first["操作"] == "更新" and first["経路"] == "管理画面（ads.google.com）"
    assert first["キャンペーン"] == "検索A"
    assert first["変わった項目"] == [{"項目": "amount（円）", "前": 1500, "後": 2000}]
    created = out["行"][2]
    assert created["操作"] == "作成" and created["変わった項目"] == [{"項目": "keyword.text", "後": "ホッケー 観戦"},
                                                                {"項目": "cpc_bid（円）", "後": 80}]
    watch = out["見ておきたい変更（自動適用・自動ルール・スクリプト・API など）"]
    assert [w["経路"] for w in watch] == ["最適化案の自動適用（購読）", "自動ルール"]
    assert out["件数"]["人が画面で変えたとは限らない経路"] == 2
    rows_text = json.dumps(out["行"], ensure_ascii=False)
    assert "micros" not in rows_text and "client_type" not in rows_text
    assert "メールアドレスは読まない" in json.dumps(out["注記"], ensure_ascii=False)
    assert Path(out["全件の保存先"]).is_file()


# ---------------- 変更の前後 ----------------

def effect_rows():
    before, after = days(date(2026, 10, 25), 7), days(date(2026, 11, 2), 7)
    rows = [day_row(1, "検索A", d, 1000, 50, 5000, 2.0) for d in before]
    rows += [day_row(1, "検索A", date(2026, 11, 1), 9999, 999, 99999, 99.0)]  # 変更日そのものは比べに入れない
    rows += [day_row(1, "検索A", d, 1000, 80, 6400, 2.0) for d in after]
    rows += [day_row(2, "小さい", d, 3, 0, 0) for d in before + after]
    return {"customer": [CUSTOMER], "campaign": rows}


def by_name(rows):
    return {r["指標"]: r for r in rows}


def test_前後の比べを手で計算した値と合わせる():
    out = tools.measure_effect("1112223333", "2026-11-01", 7, 7, run=fake_run(effect_rows()), today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert "前: 2026-10-25〜2026-10-31（7 日）／後: 2026-11-02〜2026-11-08（7 日）" in out["期間"]
    a = by_name(out["キャンペーンごと"][0]["行"])
    assert out["キャンペーンごと"][0]["対象"] == "検索A"
    # 件数: 前 350・後 560 クリック（1 日 50 → 80、+60%）
    assert a["クリック数"]["前（合計）"] == 350 and a["クリック数"]["後（合計）"] == 560
    assert a["クリック数"]["差（1 日あたり、%）"] == 60.0
    assert a["クリック数"]["判定"] == "偶然の範囲を超える差（上）"
    assert a["表示回数"]["判定"].startswith("偶然の範囲の中")
    # 費用: 35,000 円 → 44,800 円。金額は判定しない
    assert a["費用（円）"]["前（合計）"] == 35000 and a["費用（円）"]["後（合計）"] == 44800
    assert a["費用（円）"]["判定"].startswith("判定しない")
    # 率: クリック率 5.00% → 8.00%
    assert (a["クリック率（%）"]["前"], a["クリック率（%）"]["後"]) == (5.0, 8.0)
    assert a["クリック率（%）"]["判定"] == "偶然では説明しにくい差（上）"
    # 単価: 100 円 → 80 円（−20%）。クリックが多いので幅が重ならない
    assert (a["平均クリック単価（円）"]["前"], a["平均クリック単価（円）"]["後"]) == (100, 80)
    assert a["平均クリック単価（円）"]["差（%）"] == -20.0
    assert a["平均クリック単価（円）"]["判定"] == "偶然では説明しにくい差（下）"
    # 成果: 14 件 → 14 件。率 4.00% → 2.50%（z ≒ −1.27、p ≒ 0.20）、単価 2,500 円 → 3,200 円
    assert a["コンバージョン数"]["判定"].startswith("偶然の範囲の中")
    assert (a["コンバージョン率（%）"]["前"], a["コンバージョン率（%）"]["後"]) == (4.0, 2.5)
    assert a["コンバージョン率（%）"]["判定"] == "差があるとは言えない"
    assert 0.19 < a["コンバージョン率（%）"]["p 値"] < 0.22
    assert (a["コンバージョン単価（円）"]["前"], a["コンバージョン単価（円）"]["後"]) == (2500, 3200)
    assert a["コンバージョン単価（円）"]["差（%）"] == 28.0
    assert a["コンバージョン単価（円）"]["判定"] == "差があるとは言えない"
    # 小さいキャンペーンは判定できない
    small = by_name(out["キャンペーンごと"][1]["行"])
    assert small["クリック数"]["判定"] == "判定できない" and small["クリック率（%）"]["判定"] == "判定できない"
    assert small["表示回数"]["判定"].startswith("偶然の範囲の中")  # 表示は前後 21 回ずつで 10 件を超える
    # 曜日はそろっている。因果とは言わない
    assert all(v["前"] == v["後"] == 1 for v in out["曜日の数"].values())
    assert "因果の判定ではない" in out["これは何か"]
    assert any("原因だとは言えない" in n for n in out["注記"])
    assert not any("曜日の構成が違う" in n for n in out["注記"])
    assert all("確かめ" in c for c in out["根拠"])


def test_前後の比べは成果の遅れの日を成果の系から外す():
    rows = effect_rows()
    rows["campaign"] = [day_row(1, "検索A", d, 1000, 50, 5000, 3.0) for d in days(date(2026, 11, 5), 14)]
    out = tools.measure_effect("1112223333", "2026-11-12", 7, 7, level="account", run=fake_run(rows), today=TODAY)
    assert "キャンペーンごと" not in out
    t = by_name(out["合計"])
    assert t["クリック数"]["後（合計）"] == 300  # 後は昨日（11-18）までの 6 日
    assert out["条件"]["成果を確定とみなした最後の日"] == "2026-11-11"
    for name in ("コンバージョン数", "コンバージョン率（%）", "コンバージョン単価（円）"):
        assert t[name]["判定"] == "判定できない" and "確定" in t[name]["理由"]
    notes = " ".join(out["注記"])
    assert "6 日で比べた" in notes and "未確定" in notes and "曜日の構成が違う" in notes and "skip_days" in notes


def test_前後の比べは件数が少ないと判定できない():
    rows = {"customer": [CUSTOMER],
            "campaign": [day_row(1, "検索A", d, 4, 1 if i % 4 == 0 else 0, 100, 0.0) for i, d in enumerate(days(date(2026, 10, 18), 28))]}
    out = tools.measure_effect("1112223333", "2026-11-01", run=fake_run(rows), today=TODAY)
    t = by_name(out["合計"])
    for name in ("クリック数", "クリック率（%）", "平均クリック単価（円）", "コンバージョン数", "コンバージョン率（%）"):
        assert t[name]["判定"] == "判定できない", name
    assert any("効かなかった" in n for n in out["注記"])


def test_前後の比べの入力の誤り():
    run = fake_run({"customer": [CUSTOMER]})
    with pytest.raises(ValueError, match="今日"):
        tools.measure_effect("1112223333", "2026-11-19", run=run, today=TODAY)
    with pytest.raises(ValueError, match="実績がまだ無い"):
        tools.measure_effect("1112223333", "2026-11-18", run=run, today=TODAY)
    with pytest.raises(ValueError, match="1〜90"):
        tools.measure_effect("1112223333", "2026-11-01", days_before=0, run=run, today=TODAY)
    with pytest.raises(ValueError, match="キャンペーン ID"):
        tools.measure_effect("1112223333", "2026-11-01", campaign_id="1 OR 1=1", run=run, today=TODAY)
    with pytest.raises(ValueError, match="単位"):
        tools.measure_effect("1112223333", "2026-11-01", level="keyword", run=run, today=TODAY)


def test_キャンペーンIDで絞ると問い合わせに入る():
    seen = []
    tools.measure_effect("1112223333", "2026-11-01", campaign_id="123", run=fake_run({"customer": [CUSTOMER]}, seen),
                         today=TODAY)
    assert any("AND campaign.id = 123" in q for q in seen)


# ---------------- いつもの水準からの外れ ----------------

def anomaly_rows():
    end = date(2026, 11, 18)
    base = days(end - timedelta(days=28), 28)
    prev = end - timedelta(days=1)
    rows = []
    # A: 配信が止まった（見た日の行が無い ＝ 表示 0）
    rows += [day_row(1, "止まった", d, 200, 20, 900, 1.0) for d in base]
    # B: 2 日続けて日予算の 1.7 倍
    rows += [day_row(2, "使いすぎ", d, 300, 30, 1700 if d == prev else 900, 1.0) for d in base]
    rows += [day_row(2, "使いすぎ", end, 300, 30, 1700, 1.0)]
    # C: 小さすぎて判定できない
    rows += [day_row(3, "小さい", d, 2, 0, 10) for d in base + [end]]
    # D: 一時停止は対象外
    rows += [day_row(4, "止めている", d, 500, 50, 900, status="PAUSED") for d in base]
    # E: 前日と見た日の表示とクリックが下に外れる（2 日続く）
    rows += [day_row(5, "下振れ", d, 600 if d == prev else 1000, 30 if d == prev else 50, 500, 1.0) for d in base]
    rows += [day_row(5, "下振れ", end, 600, 30, 500, 1.0)]
    # F: 成果 0 件が 4 日続く（1 日 25/28 件なら 3 ÷ 0.89 ＝ 3.4 → 4 日で 5% 未満）
    rows += [day_row(6, "成果なし", d, 300, 30, 500, 0.0 if d >= end - timedelta(days=3) else 1.0) for d in base]
    rows += [day_row(6, "成果なし", end, 300, 30, 500, 0.0)]
    return {"customer": [CUSTOMER], "campaign": rows}


def test_外れを重さの順に返し判定できないものも返す():
    out = tools.get_anomalies("1112223333", run=fake_run(anomaly_rows()), today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert "見た日: 2026-11-18" in out["期間"] and "2026-10-21〜2026-11-17（28 日）" in out["期間"]
    found = {(r["対象"], r["点検"]): r for r in out["所見"]}
    assert found[("止まった", "D1 配信の停止")]["重さ"] == "大"
    d2 = found[("使いすぎ", "D2 費用の使いすぎ")]
    assert d2["重さ"] == "大" and d2["根拠の数字"]["見た日の費用（円）"] == 1700
    assert "変更履歴" in d2["確かめること"]
    assert found[("下振れ", "表示回数の下振れ")]["重さ"] == "中"
    assert found[("下振れ", "クリック数の下振れ")]["重さ"] == "中"
    d4 = found[("成果なし", "D4 成果の急変")]
    assert d4["重さ"] == "大" and d4["根拠の数字"]["0 件の連続"] == 4 and d4["根拠の数字"]["偶然で 5% 未満になる日数"] == 4
    assert "入札は触らない" in d4["確かめること"]
    weights = [r["重さ"] for r in out["所見"]]
    assert weights == sorted(weights, key=monitor.WEIGHT_ORDER.get)
    assert all(r["誰の意見"] == "AI の提案（未承認）" for r in out["所見"])
    assert all("確かめ" in r["知識ベースの根拠"] for r in out["所見"])
    unknown = {(r["対象"], r["点検"]) for r in out["判定できなかったもの"]}
    assert ("小さい", "表示回数の下振れ") in unknown and ("小さい", "D4 成果の急変") in unknown
    assert ("小さい", "D3 費用の使えなさ") in unknown
    assert not any(r["対象"] == "止めている" for r in out["所見"] + out["判定できなかったもの"])
    assert any("止めている" in n for n in out["注記"])
    # 1 日だけの外れは鳴らさない（使いすぎ の前日以外の日）。使いすぎ は表示・クリックは問題なし
    assert ("使いすぎ", "表示回数の下振れ") not in found
    assert out["まとめ"]["所見"]["大"] == 3
    assert "micros" not in json.dumps(out, ensure_ascii=False)


def test_外れの1日だけの上振れは所見にしない_アカウント単位():
    end = date(2026, 11, 18)
    rows = [day_row(1, "検索A", d, 300, 30, 1700 if d == end else 900, 1.0) for d in days(end - timedelta(days=28), 29)]
    out = tools.get_anomalies("1112223333", "2026-11-18", run=fake_run({"customer": [CUSTOMER], "campaign": rows}), today=TODAY)
    assert not any(r["点検"].startswith("D2") and r["結果"] == "所見あり" for r in out["所見"])
    acct = tools.get_anomalies("1112223333", "2026-11-18", level="account",
                               run=fake_run({"customer": [CUSTOMER], "campaign": rows}), today=TODAY)
    assert acct["見た日の数字"][0]["対象"] == "アカウント全体"
    assert not any(r["点検"].startswith("D2") for r in acct["所見"] + acct["判定できなかったもの"])  # 予算はキャンペーン単位だけ


def test_外れの入力の誤り():
    run = fake_run({"customer": [CUSTOMER]})
    with pytest.raises(ValueError, match="昨日"):
        tools.get_anomalies("1112223333", "2026-11-19", run=run, today=TODAY)
    with pytest.raises(ValueError, match="7〜90"):
        tools.get_anomalies("1112223333", lookback_days=3, run=run, today=TODAY)
    with pytest.raises(ValueError, match="単位"):
        tools.get_anomalies("1112223333", level="ad_group", run=run, today=TODAY)


def test_道具の値を手で確かめる():
    # 件数: 前 350（7 日）なら後 7 日の期待は 350。560 は範囲の外
    r = monitor.count_test(350, 7, 560, 7)
    assert r["前の水準なら"] == 350.0 and r["偶然で出る範囲"][1] < 560
    assert monitor.count_test(4, 7, 3, 7)["判定"] == "判定できない"
    assert monitor.count_test(0, 7, 20, 7)["理由"].startswith("前の期間が 0 件")
    assert monitor.rate_test(5, 10, 20, 10)["判定"] == "判定できない"  # 成果が分母より多い


def test_新しいキャンペーンは比べる相手が足りないと返し_予算の点検だけする():
    end = date(2026, 11, 18)
    rows = [day_row(1, "新しい", d, 100, 10, 1700, 0.0) for d in days(end - timedelta(days=3), 4)]
    out = tools.get_anomalies("1112223333", run=fake_run({"customer": [CUSTOMER], "campaign": rows}), today=TODAY)
    checks = {r["点検"] for r in out["所見"] + out["判定できなかったもの"]}
    assert checks == {"いつもの水準", "D2 費用の使いすぎ"}
    unknown = out["判定できなかったもの"][0]
    assert "3 日しか無い" in unknown["理由"]
    assert out["所見"][0]["点検"] == "D2 費用の使いすぎ" and out["所見"][0]["重さ"] == "大"
