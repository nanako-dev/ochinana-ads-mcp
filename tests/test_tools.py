"""道具の返答の形を、にせの API の行で確かめる。本物の API は呼ばない。"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import ochinana_ads
from ochinana_ads import config, tools


def enum(name):
    return NS(name=name)


def metrics(imp=0, clicks=0, yen=0, conv=0.0, value=0.0):
    return NS(impressions=imp, clicks=clicks, cost_micros=yen * 1_000_000, conversions=conv, conversions_value=value)


CUSTOMER = NS(customer=NS(id=1112223333, descriptive_name="テスト用", currency_code="JPY",
                          time_zone="Asia/Tokyo", manager=False))


def fake_run(tables):
    """問い合わせ文の FROM を見て、用意した行を返す。"""

    def run(customer_id, query):
        resource = query.split(" FROM ")[1].split()[0]
        return tables.get(resource, [])

    return run


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    tools._accounts.clear()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    yield
    tools._accounts.clear()


def test_実績の返答は冒頭に対象と期間と単位():
    run = fake_run({
        "customer": [CUSTOMER],
        "campaign": [
            NS(campaign=NS(name="検索A", status=enum("PAUSED"), advertising_channel_type=enum("SEARCH")),
               metrics=metrics(215, 8, 9919)),
            NS(campaign=NS(name="配信なし", status=enum("ENABLED"), advertising_channel_type=enum("SEARCH")),
               metrics=metrics()),
        ],
    })
    out = tools.get_performance("111-222-3333", "2026-05-01", "2026-11-03", "キャンペーン", run=run)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["対象アカウント"] == "1112223333（テスト用）"
    assert "2026-05-01〜2026-11-03" in out["期間"]
    assert "円" in out["単位"]
    assert out["合計"]["費用（円）"] == 9919
    assert out["行"] == [{
        "キャンペーン": "検索A", "種類": "検索", "状態": "一時停止", "表示回数": 215, "クリック数": 8,
        "費用（円）": 9919, "クリック率（%）": 3.72, "平均クリック単価（円）": 1240,
        "コンバージョン数": 0.0, "コンバージョン単価（円）": None, "コンバージョン値（円）": 0,
    }]
    assert "micros" not in json.dumps(out, ensure_ascii=False)


def test_円以外のアカウントは数字を返さない():
    usd = NS(customer=NS(id=1112223333, descriptive_name="x", currency_code="USD", time_zone="America/New_York", manager=False))
    with pytest.raises(ValueError, match="USD"):
        tools.get_performance("1112223333", "2026-10-01", "2026-10-31", run=fake_run({"customer": [usd]}))


def test_許可していないアカウントはAPIを呼ぶ前に断る():
    def run(customer_id, query):
        raise AssertionError("API を呼んではいけない")

    for fn, args in [
        (tools.get_performance, ("1234567890", "2026-10-01", "2026-10-31")),
        (tools.get_search_terms, ("1234567890", "2026-10-01", "2026-10-31")),
        (tools.get_keywords, ("1234567890",)),
    ]:
        with pytest.raises(ValueError, match="許可リスト"):
            fn(*args, run=run)


def test_検索語句は上位だけ返し全件はCSVに保存する(tmp_path):
    terms = [
        NS(campaign=NS(name="検索A"), ad_group=NS(name="G1"),
           search_term_view=NS(search_term=f"語句{i}", status=enum("NONE")), metrics=metrics(10 * i, i, 100 * i))
        for i in range(1, 31)
    ]
    out = tools.get_search_terms("1112223333", "2026-10-12", "2026-11-08",
                                 run=fake_run({"customer": [CUSTOMER], "search_term_view": terms}))
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert len(out["行"]) == 20
    assert out["行"][0]["検索語句"] == "語句30"
    assert out["合計"]["費用（円）"] == sum(100 * i for i in range(1, 31))
    saved = Path(out["全件の保存先"])
    assert saved.parent == tmp_path
    assert len(saved.read_text(encoding="utf-8-sig").strip().splitlines()) == 31  # 見出し + 30 件


def test_キーワードは除外を別の欄で返す():
    def crit(text, negative, status="ENABLED"):
        return NS(keyword=NS(text=text, match_type=enum("BROAD")), status=enum(status), negative=negative)

    run = fake_run({
        "customer": [CUSTOMER],
        "ad_group_criterion": [
            NS(campaign=NS(name="検索A"), ad_group=NS(name="G1"), ad_group_criterion=crit("ホッケー 初心者", False)),
            NS(campaign=NS(name="検索A"), ad_group=NS(name="G1"), ad_group_criterion=crit("中古", True)),
        ],
        "campaign_criterion": [
            NS(campaign=NS(name="検索A"), campaign_criterion=NS(keyword=NS(text="求人", match_type=enum("PHRASE")), negative=True)),
        ],
        "shared_criterion": [
            NS(shared_set=NS(id=7, name="共通の除外", type_=enum("NEGATIVE_KEYWORDS")),
               shared_criterion=NS(keyword=NS(text="used", match_type=enum("EXACT")))),
        ],
        "campaign_shared_set": [NS(campaign=NS(name="検索A"), shared_set=NS(id=7))],
    })
    out = tools.get_keywords("1112223333", run=run)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert [r["キーワード"] for r in out["キーワード"]] == ["ホッケー 初心者"]
    assert sorted(r["キーワード"] for r in out["除外キーワード"]) == ["used", "中古", "求人"]
    assert all(r["negative"] is False for r in out["キーワード"])
    assert all(r["negative"] is True for r in out["除外キーワード"])
    assert out["件数"]["キーワード"] == 1 and out["件数"]["除外キーワード"] == 3
    # 全件の CSV も、普通と除外でファイルを分ける
    assert set(out["全件の保存先"]) == {"キーワード", "除外キーワード"}
    assert "中古" not in Path(out["全件の保存先"]["キーワード"]).read_text(encoding="utf-8-sig")


def test_原則は文書の中身をそのまま返す():
    text = tools.get_principles()
    assert text == config.PRINCIPLES_PATH.read_text(encoding="utf-8")
    assert "品質が先" in text


def test_変更する呼び出しをどこにも持たない():
    """読み取りだけの約束を、コードの文字で確かめる。"""
    pkg = Path(inspect.getfile(ochinana_ads)).parent
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8").lower()
        assert "mutate" not in src, f"{py.name} に変更の呼び出しがある"
        assert "validate_only" not in src


def test_鍵をこのフォルダに置いていない():
    root = config.ROOT
    # node_modules は README の手順で入れる Lighthouse の依存（鍵ではない設定ファイルの .yml を含む）なので見ない
    hits = [p for p in root.rglob("*") if ".venv" not in p.parts and "node_modules" not in p.parts
            and p.suffix in (".yaml", ".yml") or p.name == "oauth_client.json"]
    assert hits == []
