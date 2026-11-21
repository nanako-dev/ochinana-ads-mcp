"""CSV の入口（csvin.read と report_numbers_csv）。小さな CSV を一時フォルダに作って確かめる。API は呼ばない。"""
from datetime import date

import pytest

from ochinana_ads import config, csvin, numcheck, report, tools

TODAY = date(2026, 11, 19)


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path / "out"))


GOOGLE_JA = """キャンペーン レポート
2026年11月1日～2026年11月14日
日,キャンペーン,キャンペーン ID,通貨コード,表示回数,クリック数,費用,コンバージョン,コンバージョン値,クリック率
2026-11-01,ブランド,111,JPY,"1,000",50,"¥5,000",2.00,"10,000",5.00%
2026-11-01,一般,222,JPY,500,10,"1,200.50",0.50,--,2.00%
2026-11-08,ブランド,111,JPY,"1,200",60,"¥6,000",3.00,"12,000",5.00%
2026-11-08,一般,222,JPY,400,8,"1,000",--,--,2.00%

合計: アカウント,--,--,JPY,"3,100",128,"13,200.50",5.50,"22,000",4.13%
"""

META_EN = """Reporting starts,Reporting ends,Campaign name,Impressions,Link clicks,Clicks (all),Amount spent (JPY),Results,Purchases conversion value
2026-11-08,2026-11-08,Spring,2000,40,70,3000,4,8000
2026-11-09,2026-11-09,Spring,1500,30,50,2500,1,2000
2026-11-08,2026-11-09,,3500,70,120,5500,5,10000
"""


def write(tmp_path, name, text, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return str(p)


def test_Google_の日本語の書き出し_表題と合計の行を飛ばす(tmp_path):
    out = csvin.read(write(tmp_path, "google.csv", GOOGLE_JA, "utf-8-sig"))
    rows, how = out["行"], out["読み方"]
    assert how["見出しの行"] == 3 and how["文字コード"] == "UTF-8"
    assert how["列の対応"]["日付"] == "日" and how["列の対応"]["費用"] == "費用" and how["列の対応"]["キャンペーン ID"] == "キャンペーン ID"
    assert how["飛ばした行"]["合計の行"] == 1 and how["飛ばした行"]["空の行"] == 1
    assert len(rows) == 4 and how["媒体"] == ["Google 広告"]  # 列の名前から推定
    r = rows[0]
    assert r == {"date": "2026-11-01", "key": "111", "name": "ブランド", "media": "Google 広告", "impressions": 1000,
                 "clicks": 50, "cost": 5000.0, "conversions": 2.0, "value": 10000.0}
    assert rows[1]["cost"] == 1200.5 and rows[1]["value"] == 0.0
    assert rows[3]["conversions"] == 0.0  # 「--」は 0
    assert any("推定" in n for n in how["注意"])
    assert "クリック率" in how["使わなかった列"]


def test_Meta_の英語の書き出し(tmp_path):
    out = csvin.read(write(tmp_path, "meta.csv", META_EN), media="meta")
    rows, how = out["行"], out["読み方"]
    assert how["列の対応"]["日付"] == "Reporting starts"
    assert how["列の対応"]["クリック数"] == "Link clicks"          # リンクのクリックを、すべてのクリックより先に使う
    assert how["使わなかった同じ種類の列"]["クリック数"] == ["Clicks (all)"]
    assert how["列の対応"]["費用"] == "Amount spent (JPY)" and how["列の対応"]["コンバージョン"] == "Results"
    assert how["飛ばした行"]["合計の行"] == 1                     # キャンペーン名の無い行
    assert [r["media"] for r in rows] == ["Meta", "Meta"] and rows[0]["key"] == "Spring"
    assert rows[0]["value"] == 8000.0
    assert any("目的ごと" in n for n in how["注意"])


def test_Shift_JIS_とタブ区切り(tmp_path):
    text = "日付\tキャンペーン名\tインプレッション数\tクリック数\tコスト\tコンバージョン数\n2026/11/3\t検索A\t300\t9\t900\t1\n"
    out = csvin.read(write(tmp_path, "line.tsv", text, "cp932"), media="LINEヤフー")
    assert out["読み方"]["文字コード"].startswith("Shift_JIS") and out["読み方"]["区切り"] == "タブ"
    r = out["行"][0]
    assert r["date"] == "2026-11-03" and r["media"] == "LINEヤフー" and r["cost"] == 900.0 and r["impressions"] == 300


def test_UTF16_と日本語の日付(tmp_path):
    text = "日\tキャンペーン\t表示回数\tクリック数\t費用\tコンバージョン\n2026年11月5日\tA\t10\t1\t¥100\t0\n"
    out = csvin.read(write(tmp_path, "g16.csv", text, "utf-16"))
    assert out["読み方"]["文字コード"] == "UTF-16" and out["行"][0]["date"] == "2026-11-05"


def test_足りない列は名前を挙げて断る(tmp_path):
    text = "日,キャンペーン,表示回数,クリック数,コンバージョン\n2026-11-01,A,10,1,0\n"
    with pytest.raises(ValueError) as ex:
        csvin.read(write(tmp_path, "nocost.csv", text))
    msg = str(ex.value)
    assert "費用" in msg and "見出しの列" in msg and "キャンペーン" in msg


def test_日付の列が無ければ断る(tmp_path):
    text = "キャンペーン,表示回数,クリック数,費用,コンバージョン\nA,10,1,100,0\n"
    with pytest.raises(ValueError, match="日付の列が無い"):
        csvin.read(write(tmp_path, "nodate.csv", text))


def test_列の名前を指定して読む(tmp_path):
    text = "集計日,広告名,見られた数,押された数,ご利用金額,獲得\n2026-11-02,A,100,5,500,1\n"
    with pytest.raises(ValueError):
        csvin.read(write(tmp_path, "odd.csv", text))
    cols = {"date": "集計日", "campaign": "広告名", "impressions": "見られた数", "clicks": "押された数",
            "cost": "ご利用金額", "conversions": "獲得"}
    out = csvin.read(str(tmp_path / "odd.csv"), media="ChatGPT広告", columns=cols)
    r = out["行"][0]
    assert r["cost"] == 500.0 and r["clicks"] == 5 and r["media"] == "ChatGPT 広告"
    assert out["読み方"]["列の対応"]["費用"] == "ご利用金額"
    with pytest.raises(ValueError, match="欄の名前が分からない"):
        csvin.read(str(tmp_path / "odd.csv"), columns={"spend_yen": "ご利用金額"})


def test_円以外と_csv_でないファイルを断る(tmp_path):
    text = "Day,Campaign,Impressions,Clicks,Cost,Conversions,Currency code\n2026-11-01,A,10,1,1,0,USD\n"
    with pytest.raises(ValueError, match="円ではない"):
        csvin.read(write(tmp_path, "usd.csv", text))
    with pytest.raises(ValueError, match=".csv か .tsv"):
        csvin.read(write(tmp_path, "x.txt", text))
    with pytest.raises(ValueError, match="見つからない"):
        csvin.read(str(tmp_path / "none.csv"))


def test_report_numbers_csv_の合計が手で計算した数字と合う(tmp_path):
    path = write(tmp_path, "google.csv", GOOGLE_JA, "utf-8-sig")
    b = tools.report_numbers_csv(path, "2026-11-08", "2026-11-14", compare="week", today=TODAY)
    assert b["対象アカウント"] == "CSV: google.csv（Google 広告）"
    t = {r["指標"]: r for r in b["合計"]}
    # 今（11/08〜11/14）: 表示 1,200+400、クリック 60+8、費用 6,000+1,000、CV 3+0、値 12,000
    assert t["表示回数"]["今"] == 1600 and t["クリック数"]["今"] == 68 and t["費用"]["今"] == 7000
    assert t["コンバージョン"]["今"] == 3.0 and t["コンバージョン値"]["今"] == 12000
    assert t["コンバージョン単価"]["今"] == round(7000 / 3) and t["クリック率"]["今"] == round(68 / 1600 * 100, 2)
    assert t["ROAS"]["今"] == round(12000 / 7000 * 100, 1)
    # 前（11/01〜11/07）: 表示 1,500、クリック 60、費用 6,200.5 → 6,201 円（四捨五入）、CV 2.5
    assert t["表示回数"]["前"] == 1500 and t["クリック数"]["前"] == 60 and t["費用"]["前"] == 6201
    assert t["コンバージョン"]["前"] == 2.5 and t["費用"]["差"] == round(7000 - 6200.5)
    assert b["CSV の読み方"]["読んだ行"] == 4
    assert any("CSV に行が無い" in n for n in b["注意"])           # 11/09〜11/14 は行が無い
    data = numcheck.load_file(b["束ID"])
    assert len(data["元の行"]) == 4 and data["元の行"][0]["key"] == "111"
    assert "表" in b and "日別" in b["表"]


def test_report_numbers_csv_比較期間が無くても止めない(tmp_path):
    path = write(tmp_path, "meta.csv", META_EN)
    b = tools.report_numbers_csv(path, "2026-11-08", "2026-11-09", media="Meta", today=TODAY)
    assert "比較期間（2026-11-06〜2026-11-07）の行が CSV に無い" in b["注意"][0]
    t = {r["指標"]: r for r in b["合計"]}
    assert t["費用"]["今"] == 5500 and t["費用"]["前"] == 0 and t["クリック数"]["今"] == 70
    assert b["対象アカウント"] == "CSV: meta.csv（Meta）"
    with pytest.raises(ValueError, match="今の期間"):
        tools.report_numbers_csv(path, "2026-10-01", "2026-10-07", today=TODAY)
    with pytest.raises(ValueError, match="昨日"):
        tools.report_numbers_csv(path, "2026-11-08", "2026-11-19", today=TODAY)
    assert report.LAG_DAYS == 7
