"""円への換算、合計と上位、除外キーワードの分離。API は呼ばない。"""
import csv
import json

import pytest

from ochinana_ads import convert


def test_microsを円に直す():
    assert convert.micros_to_yen(0) == 0
    assert convert.micros_to_yen(1_000_000) == 1
    assert convert.micros_to_yen(9_919_000_000) == 9919
    assert convert.micros_to_yen(1_240_000_000) == 1240


def test_1円未満は四捨五入():
    assert convert.micros_to_yen(499_999) == 0
    assert convert.micros_to_yen(500_000) == 1
    assert convert.micros_to_yen(1_500_000) == 2
    assert convert.micros_to_yen(2_500_000) == 3  # 銀行丸め（2）にしない
    assert convert.micros_to_yen(123_456_789) == 123


def test_円以外の通貨は断る():
    convert.require_jpy("JPY")
    with pytest.raises(ValueError, match="USD"):
        convert.require_jpy("USD")


def test_実績は日本語の列名と円で返す():
    m = convert.metrics_ja(
        {"impressions": 215, "clicks": 8, "cost_micros": 9_919_000_000, "conversions": 0.0, "conversions_value": 0.0}
    )
    assert m["表示回数"] == 215
    assert m["クリック数"] == 8
    assert m["費用（円）"] == 9919
    assert m["クリック率（%）"] == 3.72
    assert m["平均クリック単価（円）"] == 1240
    assert m["コンバージョン数"] == 0
    assert m["コンバージョン単価（円）"] is None


def test_割れないときはNone():
    m = convert.metrics_ja({"impressions": 0, "clicks": 0, "cost_micros": 0, "conversions": 0, "conversions_value": 0})
    assert m["クリック率（%）"] is None
    assert m["平均クリック単価（円）"] is None
    assert m["コンバージョン単価（円）"] is None


def test_コンバージョン単価と値():
    m = convert.metrics_ja(
        {"impressions": 1000, "clicks": 50, "cost_micros": 30_000_000_000, "conversions": 4.0, "conversions_value": 12000.4}
    )
    assert m["コンバージョン単価（円）"] == 7500
    assert m["コンバージョン値（円）"] == 12000


def _row(name, imp, clicks, yen):
    return {"キャンペーン": name, "impressions": imp, "clicks": clicks, "cost_micros": yen * 1_000_000,
            "conversions": 0.0, "conversions_value": 0.0}


def test_返答にmicrosを残さない():
    rows = [_row("A", 100, 5, 500), _row("B", 300, 9, 1200), _row("C", 0, 0, 0)]
    body, active = convert.report(rows, top_n=20)
    text = json.dumps(body, ensure_ascii=False)
    assert "micros" not in text
    assert "impressions" not in text and "clicks" not in text
    for r in body["行"]:
        assert "費用（円）" in r and "キャンペーン" in r


def test_合計は全件で上位だけ表示する():
    rows = [_row(f"c{i}", 10 * i, i, 100 * i) for i in range(1, 31)]
    body, active = convert.report(rows, top_n=20)
    assert len(body["行"]) == 20
    assert len(active) == 30
    assert body["行"][0]["キャンペーン"] == "c30"  # 費用の多い順
    assert body["合計"]["費用（円）"] == sum(100 * i for i in range(1, 31))
    assert body["合計"]["クリック数"] == sum(range(1, 31))
    assert body["件数"] == {"実績あり": 30, "表示している件数": 20, "実績ゼロで省いた件数": 0}


def test_実績ゼロの行は省いて数だけ返す():
    body, active = convert.report([_row("A", 100, 5, 500), _row("Z", 0, 0, 0)], top_n=20)
    assert [r["キャンペーン"] for r in body["行"]] == ["A"]
    assert body["件数"]["実績ゼロで省いた件数"] == 1


def test_並び順を変えられる():
    rows = [_row("費用が多い", 10, 1, 900), _row("表示が多い", 999, 2, 100), _row("クリックが多い", 50, 30, 200)]
    assert convert.sort_rows(rows, "費用")[0]["キャンペーン"] == "費用が多い"
    assert convert.sort_rows(rows, "表示")[0]["キャンペーン"] == "表示が多い"
    assert convert.sort_rows(rows, "クリック")[0]["キャンペーン"] == "クリックが多い"
    with pytest.raises(ValueError):
        convert.sort_rows(rows, "cost")


def test_表示件数は範囲に収める():
    assert convert.clamp_top_n(20, 20, 100) == 20
    assert convert.clamp_top_n(0, 20, 100) == 1
    assert convert.clamp_top_n(5000, 20, 100) == 100
    assert convert.clamp_top_n(None, 20, 100) == 20


def _kw(text, negative, where="広告グループ"):
    return {"キーワード": text, "一致方式": "部分一致", "状態": "有効", "置き場所": where, "negative": negative}


def test_除外キーワードは別の欄に分ける():
    rows = [
        _kw("ホッケー 初心者", False),
        _kw("中古", True),
        _kw("ホッケー ルール", False),
        _kw("求人", True, "キャンペーン"),
        _kw("used", True, "共有の除外リスト「共通」"),
    ]
    normal, negative = convert.split_keywords(rows)
    assert [r["キーワード"] for r in normal] == ["ホッケー 初心者", "ホッケー ルール"]
    assert [r["キーワード"] for r in negative] == ["中古", "求人", "used"]
    assert all(r["negative"] is False for r in normal)
    assert all(r["negative"] is True for r in negative)
    # 状態が「有効」・一致方式が「部分一致」でも、除外は普通の側に入らない
    assert not {r["キーワード"] for r in normal} & {"中古", "求人", "used"}


def test_除外かどうか分からない行は例外にする():
    with pytest.raises(ValueError, match="negative"):
        convert.split_keywords([{"キーワード": "中古", "状態": "有効"}])
    with pytest.raises(ValueError, match="negative"):
        convert.split_keywords([{"キーワード": "中古", "negative": None}])


def test_除外キーワードが無いときは空の欄():
    normal, negative = convert.split_keywords([_kw("ホッケー", False)])
    assert len(normal) == 1 and negative == []


def test_知らない値は日本語にせずそのまま返す():
    assert convert.ja(convert.STATUS_JA, "ENABLED") == "有効"
    assert convert.ja(convert.MATCH_JA, "BROAD") == "部分一致"
    assert convert.ja(convert.STATUS_JA, "SOMETHING_NEW") == "SOMETHING_NEW"


def test_いちばん多い指定を選ぶ():
    assert convert.pick_most_common(["geoTargetConstants/2392"] * 3 + ["geoTargetConstants/2840"]) == "geoTargetConstants/2392"
    assert convert.pick_most_common([]) is None
    assert convert.pick_most_common(["b", "a"]) == "a"  # 同数なら毎回同じものを返す


def test_CSVに全件を保存する(tmp_path):
    rows = [convert.to_ja_row(_row("A", 100, 5, 500)), convert.to_ja_row(_row("B", 300, 9, 1200))]
    path = convert.write_csv(tmp_path / "sub" / "x.csv", rows)
    with open(path, encoding="utf-8-sig", newline="") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 2
    assert got[1]["費用（円）"] == "1200"
    assert "cost_micros" not in got[0]
