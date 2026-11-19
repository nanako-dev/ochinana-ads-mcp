"""計算の道具。手で計算した値と照らす。API は呼ばない。"""
import math

import pytest

from ochinana_ads import stats


def test_必要な量_2パーセントから3パーセント():
    # 手計算: z = 1.959964 + 0.841621 = 2.801585、z² = 7.848879
    #        分散の和 = 0.02×0.98 + 0.03×0.97 = 0.0487、差² = 0.0001
    #        n = 7.848879 × 0.0487 ÷ 0.0001 = 3822.4 → 切り上げて 3823
    assert stats.sample_size_per_variant(0.02, 0.03) == 3823
    out = stats.data_needed(0.02, 0.03, 1000)
    assert 3800 <= out["1 案あたりに必要な量"] <= 3900
    assert out["1 案あたりに必要な量"] == 3823
    assert out["2 案の合計で必要な量"] == 7646
    assert out["かかる日数"] == 8  # 7646 ÷ 1000 = 7.646 → 8 日
    assert out["扱い"] == "テストする"


def test_必要な量_10パーセントから12パーセント():
    # 分散の和 = 0.09 + 0.1056 = 0.1956、差² = 0.0004 → 7.848879 × 489 = 3838.1 → 3839
    assert stats.sample_size_per_variant(0.10, 0.12) == 3839


def test_下がる方向でも同じ量になる():
    assert stats.sample_size_per_variant(0.03, 0.02) == stats.sample_size_per_variant(0.02, 0.03)


def test_検出力と有意水準を変えると量が変わる():
    base = stats.sample_size_per_variant(0.02, 0.03)
    assert stats.sample_size_per_variant(0.02, 0.03, power=0.9) > base
    assert stats.sample_size_per_variant(0.02, 0.03, alpha=0.10) < base
    # 検出力 90%: z = 1.959964 + 1.281552 = 3.241516、z² = 10.507424 → 5117.1 → 5118
    assert stats.sample_size_per_variant(0.02, 0.03, power=0.9) == 5118


@pytest.mark.parametrize(
    "daily,days,handling",
    [
        (1000, 8, "テストする"),
        (547, 14, "テストする"),                       # 7646 ÷ 547 = 13.98 → 14 日（線の上）
        (546, 15, "手前の指標で見るか、途中経過で判断する"),
        (200, 39, "手前の指標で見るか、途中経過で判断する"),
        (128, 60, "手前の指標で見るか、途中経過で判断する"),  # 59.7 → 60 日（線の上）
        (127, 61, "テストしない"),
        (8, 956, "テストしない"),
    ],
)
def test_日数と扱い(daily, days, handling):
    out = stats.data_needed(0.02, 0.03, daily)
    assert out["かかる日数"] == days == math.ceil(7646 / daily)
    assert out["扱い"] == handling
    assert out["理由"]


def test_式と前提を返す():
    out = stats.data_needed(0.02, 0.03, 1000)
    assert "p1(1 − p1)" in out["式"]
    assert any("半分ずつ" in p for p in out["前提"])
    assert out["入力"]["検出力"] == 0.8 and out["入力"]["有意水準"] == 0.05


@pytest.mark.parametrize(
    "args",
    [(2, 0.03, 1000), (0.02, 3, 1000), (0, 0.03, 1000), (0.02, 0.02, 1000), (0.02, 0.03, 0),
     (0.02, 0.03, -5), ("x", 0.03, 1000), (0.02, 0.03, 1000, 1.2), (0.02, 0.03, 1000, 0.8, 0)],
)
def test_おかしな入力は断る(args):
    with pytest.raises(ValueError):
        stats.data_needed(*args)


def test_上回る確率_手で解ける場合():
    # A 0/1・B 1/1: A は Beta(1,2)、B は Beta(2,1)。P(B>A) = ∫ 2b(2b − b²) db = 4/3 − 1/2 = 5/6
    assert stats.probability_b_beats_a(0, 1, 1, 1) == pytest.approx(5 / 6, abs=1e-9)
    # A 0/2・B 2/2: 1 − 3×B(3,4) = 1 − 3×(1/60) = 0.95
    assert stats.probability_b_beats_a(0, 2, 2, 2) == pytest.approx(0.95, abs=1e-9)
    # 同じ結果なら五分五分
    assert stats.probability_b_beats_a(1, 2, 1, 2) == pytest.approx(0.5, abs=1e-9)
    assert stats.probability_b_beats_a(30, 1000, 30, 1000) == pytest.approx(0.5, abs=1e-6)


def test_上回る確率_入れ替えると足して1():
    p = stats.probability_b_beats_a(20, 1000, 30, 1000)
    q = stats.probability_b_beats_a(30, 1000, 20, 1000)
    assert p + q == pytest.approx(1.0, abs=1e-6)


def test_上回る確率_件数が多いときは正規近似と合う():
    # 手計算（正規近似）: 差 0.01 ÷ √(0.0196/1000 + 0.0291/1000) = 1.433 → 0.924
    assert stats.probability_b_beats_a(20, 1000, 30, 1000) == pytest.approx(0.924, abs=0.01)
    assert stats._normal_probability(20, 1000, 30, 1000) == pytest.approx(0.9241, abs=0.0005)


def test_2案を比べる():
    out = stats.compare_variants(20, 1000, 30, 1000)
    assert out["A"]["率"] == 0.02 and out["B"]["率"] == 0.03
    assert out["差（B − A、ポイント）"] == 1.0
    assert out["差（A に対する割合、%）"] == 50.0
    assert 0.91 < out["B が A を上回る確率"] < 0.94
    assert out["件数は足りているか"] is True
    assert out["止めてよいかの目安"] == "続ける"


def test_はっきり差があれば止めてよい():
    assert stats.compare_variants(100, 1000, 150, 1000)["止めてよいかの目安"] == "B の勝ちとして止めてよい"
    assert stats.compare_variants(150, 1000, 100, 1000)["止めてよいかの目安"] == "A の勝ちとして止めてよい"


def test_件数が少ないときは判断させない():
    out = stats.compare_variants(0, 8, 3, 8)
    assert out["件数は足りているか"] is False
    assert out["止めてよいかの目安"] == "まだ判断しない"
    assert "5 件以下" in out["理由"]
    assert out["B が A を上回る確率"] > 0.9  # 確率は高く出るが、止める判断には使わせない
    assert out["差（A に対する割合、%）"] is None


@pytest.mark.parametrize("args", [(1, 0, 1, 10), (11, 10, 1, 10), (-1, 10, 1, 10), (1.5, 10, 1, 10), ("a", 10, 1, 10)])
def test_比べられない入力は断る(args):
    with pytest.raises(ValueError):
        stats.compare_variants(*args)
