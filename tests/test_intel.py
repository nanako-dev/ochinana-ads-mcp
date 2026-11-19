"""知見カードを読む道具。作り物のカードで試す（本物の ads-intel/ は読まない）。"""
from datetime import date

import pytest

from ochinana_ads import intel

TODAY = date(2026, 11, 11)


def write_card(folder, cid, summary, tier="A", trust="公式", status="未決", deadline="", settings=None,
               entered="2026-11-10", source_date="2026-11-09", kind="仕様・挙動", draft=True, title="Original title"):
    import json

    q = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    lines = ["---", f"id: {cid}", "媒体: google_ads", f"何が変わったか: {q(summary)}", "出どころ:",
             f"  url: {q('https://example.com/' + cid)}", f"  名前: {q('出どころの名前')}", f"  段: {q(tier)}",
             f"  日付: {q(source_date)}", f"関係する設定: {q(settings or [])}", "関係する基準と点検: []",
             f"知見の種類: {q(kind)}", f"信頼度: {trust}", f"扱い: {status}", f"期限: {q(deadline)}", f"入った日: {entered}"]
    if draft:
        lines.append(f"下書き: {q('AI（codex）の下書き。人の確認はまだ')}")
    lines += ["---", "", f"# {title}", "", "本文は出どころで読む。", ""]
    (folder / "cards" / f"{cid}.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(autouse=True)
def _intel(tmp_path, monkeypatch):
    (tmp_path / "cards").mkdir()
    (tmp_path / "digest").mkdir()
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "2026-11-10.jsonl").write_text('{"excerpt": "記事の本文の抜粋。外へ出さない。"}\n', encoding="utf-8")
    (tmp_path / "digest" / "2026-11-10.md").write_text("# まとめ\n", encoding="utf-8")
    (tmp_path / "digest" / "2026-10-01.md").write_text("# 古いまとめ\n", encoding="utf-8")
    monkeypatch.setenv(intel.ENV_INTEL_DIR, str(tmp_path))
    write_card(tmp_path, "2026-11-10-demandgen", "デマンド ジェネレーションのディスプレイ配信は広告グループ側で管理する。",
               deadline="2026-12-01", settings=["デマンドジェネレーション", "network_settings.target_content_network"],
               title="Display targeting changes in Demand Gen")
    write_card(tmp_path, "2026-11-10-sunset", "API v23 系は 2027 年 2 月に廃止予定。", settings=["API v23系", "API v24"],
               source_date="", title="Timetable")
    write_card(tmp_path, "2026-11-10-summary", "検索広告で AI 要約を表示するテストが報告されている。", tier="B", trust="仮説",
               settings=["検索広告", "AI生成要約"], title="AI summaries in ads")
    write_card(tmp_path, "2026-11-01-smart", "スマートキャンペーンの新規作成ができなくなった。", status="見張る",
               deadline="2026-09-23", settings=["スマートキャンペーン"], entered="2026-11-01", source_date="2026-09-23",
               draft=False, title="Smart Campaigns")
    write_card(tmp_path, "2026-11-10-other", "注目のギフトを紹介する案内。", kind="その他", settings=[], title="Gift guide")
    return tmp_path


def campaigns(*rows):
    return {"campaigns": [{"id": i, "name": n, "status": s, "channel": ch, "bidding": "MAXIMIZE_CONVERSIONS", "ai_max": ai}
                          for i, (n, s, ch, ai) in enumerate(rows)],
            "language_criteria": 1, "conversions_30d": 0.0}


STATE = campaigns(("検索A", "ENABLED", "SEARCH", True), ("DG", "PAUSED", "DEMAND_GEN", False))
FACTS = {"API の版": "v26", "google-ads ライブラリ": "35.0.0", "Python": "3.12.14"}


def test_カードを読む(_intel):
    cards = intel.load_cards()
    assert len(cards) == 5
    c = next(c for c in cards if c["id"] == "2026-11-10-demandgen")
    assert c["出どころ"]["段"] == "A" and c["出どころ"]["url"].endswith("demandgen")
    assert c["関係する設定"] == ["デマンドジェネレーション", "network_settings.target_content_network"]
    assert c["題名"] == "Display targeting changes in Demand Gen"
    assert c["扱い"] == "未決" and c["期限"] == "2026-12-01"


def test_フォルダが無ければ場所の指し方を返す(tmp_path, monkeypatch):
    monkeypatch.setenv(intel.ENV_INTEL_DIR, str(tmp_path / "none"))
    with pytest.raises(intel.IntelError, match="ADS_INTEL_DIR"):
        intel.load_cards()


def test_新しく入ったものと未決の件数():
    out = intel.get_new_intel("2026-11-05", today=TODAY)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    n = out["件数"]
    assert n["この期間に入ったカード"] == 4 and n["段ごと"] == {"A": 3, "B": 1}
    assert n["扱いが未決のカード（全期間）"] == 4 and n["うち期限つき"] == 1 and n["カードの総数"] == 5
    assert n["うち期限まで 30 日以内"] == 1 and n["うち期限を過ぎたもの"] == 0
    assert {r["id"] for r in out["新しく入ったもの"]} == {"2026-11-10-demandgen", "2026-11-10-sunset",
                                                    "2026-11-10-summary", "2026-11-10-other"}
    row = out["未決で期限つきのもの（期限の近い順）"][0]
    assert row == {"id": "2026-11-10-demandgen", "題名": "Display targeting changes in Demand Gen", "段": "A",
                   "信頼度": "公式", "知見の種類": "仕様・挙動", "出どころの日付": "2026-11-09", "入った日": "2026-11-10",
                   "扱い": "未決", "期限": "2026-12-01", "期限まで（日）": 20}
    assert [p.rsplit("/", 1)[-1] for p in out["毎日のまとめ"]] == ["2026-11-10.md"]


def test_日付を省くと7日前から_おかしな日付は断る():
    out = intel.get_new_intel(today=TODAY)
    assert "2026-11-04 以降" in out["期間"] and out["件数"]["この期間に入ったカード"] == 4
    assert intel.get_new_intel("2026-10-01", today=TODAY)["件数"]["この期間に入ったカード"] == 5
    with pytest.raises(intel.IntelError):
        intel.get_new_intel("11/05")


def test_記事の抜粋は返さない():
    import json

    text = json.dumps([intel.get_new_intel("2026-01-01", today=TODAY), intel.search_cards("")], ensure_ascii=False)
    assert "外へ出さない" not in text and "excerpt" not in text  # inbox の中身は読まない


def test_カードを語と扱いと段で探す():
    out = intel.search_cards("デマンド", today=TODAY)
    assert [r["id"] for r in out["行"]] == ["2026-11-10-demandgen"]
    row = out["行"][0]
    assert row["何が変わったか"].startswith("デマンド ジェネレーション")
    assert row["出どころ"] == {"名前": "出どころの名前", "URL": "https://example.com/2026-11-10-demandgen"}
    assert row["人の確認"] == "まだ（AI の下書き）" and row["期限まで（日）"] == 20
    assert [r["id"] for r in intel.search_cards("demand gen targeting")["行"]] == ["2026-11-10-demandgen"]  # 題名も見る・全部含む
    assert intel.search_cards("デマンド スマート")["件数"]["当たったカード"] == 0
    assert [r["id"] for r in intel.search_cards(tier="B")["行"]] == ["2026-11-10-summary"]
    assert [r["id"] for r in intel.search_cards(status="見張る")["行"]] == ["2026-11-01-smart"]
    assert intel.search_cards(status="見張る")["行"][0]["人の確認"] == "済み"
    assert intel.search_cards("")["件数"]["当たったカード"] == 5
    assert intel.search_cards("ＡＰＩ ｖ２３")["件数"]["当たったカード"] == 1  # 全角でも当たる
    with pytest.raises(intel.IntelError, match="扱い"):
        intel.search_cards(status="保留")
    with pytest.raises(intel.IntelError, match="段"):
        intel.search_cards(tier="D")


def test_カードIDで1枚を選ぶ():
    assert intel.find_card("2026-11-10-sunset")["id"] == "2026-11-10-sunset"
    assert intel.find_card("sunset")["id"] == "2026-11-10-sunset"
    with pytest.raises(intel.IntelError, match="枚ある"):
        intel.find_card("2026-11-10")
    with pytest.raises(intel.IntelError, match="見つからない"):
        intel.find_card("nothing")


def match(card_id, state=STATE, facts=FACTS):
    return intel.match_card(intel.find_card(card_id), state, facts, "1112223333（テスト用）", TODAY)


def test_当てはまる_有効なキャンペーンがある():
    out = match("summary")
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert out["判定"] == "当てはまる"
    assert out["照らした点"] == [{"カードの語": "検索広告", "見たもの": "キャンペーンの種類: 検索キャンペーン",
                             "アカウントの状態": "有効 1 本・一時停止 0 本", "判定": "当てはまる"}]
    assert out["機械で照らせなかった語"] == ["AI生成要約"]


def test_仮説と下書きは必ず伝える():
    notes = " ".join(match("summary")["必ず伝えること"])
    assert "AI の下書き" in notes and "仮説" in notes and "未決" in notes
    confirmed = match("smart")["必ず伝えること"]
    assert confirmed == []  # 人が確かめて扱いを決めた、公式のカード


def test_一時停止のものだけなら_そう返す():
    out = match("demandgen")
    assert out["判定"] == "当てはまる（一時停止のものだけ）"
    assert out["照らした点"][0]["アカウントの状態"] == "有効 0 本・一時停止 1 本"
    assert out["機械で照らせなかった語"] == ["network_settings.target_content_network"]


def test_当てはまらない_その種類のキャンペーンが無い():
    out = match("smart")
    assert out["判定"] == "当てはまらない" and "使っていない" in out["理由"]


def test_APIの版は道具が使っている版と比べる():
    out = match("sunset")
    assert out["判定"] == "当てはまらない"
    assert [r["判定"] for r in out["照らした点"]] == ["当てはまらない", "当てはまらない"]
    assert out["照らした点"][0]["アカウントの状態"] == "この道具は v26"
    old = match("sunset", facts={**FACTS, "API の版": "v23"})
    assert old["判定"] == "当てはまる"


def test_結び付けられないカードは判断できない_事実だけ並べる():
    out = match("other")
    assert out["判定"] == "判断できない" and out["照らした点"] == []
    state = out["アカウントの状態（道具で読める範囲）"]
    assert state["キャンペーンの種類ごとの本数"] == {"検索": {"有効": 1, "一時停止": 0}, "デマンド ジェネレーション": {"有効": 0, "一時停止": 1}}
    assert state["入札戦略（有効なキャンペーン）"] == ["コンバージョン数の最大化"]
    assert state["AI Max が有効なキャンペーン"] == ["検索A"]
    assert state["この道具"] == FACTS


def test_AI_Maxと言語の指定とAPIの利用(_intel):
    write_card(_intel, "2026-11-10-lang", "検索キャンペーン（AI Max を含む）で手動の言語指定が廃止される。",
               settings=["AI Max", "CampaignCriterion.language", "開発者トークン", "Python 3.9"])
    out = match("lang")
    got = {r["見たもの"]: r["判定"] for r in out["照らした点"]}
    assert got == {"AI Max を有効にしているキャンペーン": "当てはまる", "キャンペーンの言語の指定": "当てはまる",
                   "Google 広告 API を使っているか": "当てはまる", "この道具が動いている Python の版": "当てはまらない"}
    assert out["判定"] == "当てはまる"
    none = match("lang", state={**campaigns(("検索A", "ENABLED", "SEARCH", False)), "language_criteria": 0})
    got = {r["見たもの"]: r["判定"] for r in none["照らした点"]}
    assert got["AI Max を有効にしているキャンペーン"] == "当てはまらない" and got["キャンペーンの言語の指定"] == "当てはまらない"


def test_設定の語が無いカードは要点の文から見る(_intel):
    write_card(_intel, "2026-11-10-pmax", "新規 P-MAX の画像抽出が既定で有効になる。", settings=[])
    out = match("pmax")
    assert out["判定"] == "当てはまらない"
    assert out["照らした点"][0]["カードの語"] == "（要点の文から）"


def test_カードを書き換えない(_intel):
    before = {p.name: p.read_text(encoding="utf-8") for p in (_intel / "cards").glob("*.md")}
    intel.get_new_intel("2026-01-01", today=TODAY)
    intel.search_cards("検索")
    match("summary")
    after = {p.name: p.read_text(encoding="utf-8") for p in (_intel / "cards").glob("*.md")}
    assert before == after
