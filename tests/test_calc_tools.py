"""小さな道具（計算と点検）。手で計算した値と照らす。外にも API にもつながない。

ふだんは知識ベースを「無い」場所に向けて、道具が知識ベース無しでも動くことを確かめる。
最後に、実物の知識ベースがあれば、道具が根拠にしている行が全部まだ書いてあるかを確かめる。
"""
import asyncio
import json
import struct
from pathlib import Path

import pytest

from ochinana_ads import calc, feed, kb, server, specs, stats, urltool


@pytest.fixture(autouse=True)
def _no_kb(monkeypatch, tmp_path):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(tmp_path / "no-kb"))


# ---------------- ad_text_check ----------------

def test_文字数は半角1全角2で数える():
    assert specs.width("abc") == 3
    assert specs.width("ホッケー") == 8
    assert specs.width("ＡＢ1") == 5
    assert specs.width("ｱｲ") == 2  # 半角カタカナは 1（ただし無効な文字として知らせる）


def test_RSAの上限と本数と編集基準():
    out = specs.ad_text_check("rsa", {
        "headlines": ["ホッケーのルールを解説", "ホッケーのルールを解説", "今だけ！！セール★", "ｶﾞｲﾄﾞ😀", "あ" * 16],
        "説明文": ["観戦の前に読む 3 分のガイド。"],
        "path2": "rules",
    })
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    rows = out["行"]
    assert rows[0]["文字数（半角換算）"] == 22 and rows[0]["上限"] == 30 and rows[0]["残り"] == 8
    assert rows[4]["判定"] == "超過（2 超える）"
    f = " ".join(out["所見"])
    assert "見出し 2: 見出し 1 と同じ文（重複）" in f
    assert "同じ記号の繰り返し「！！」" in f and "★" in f
    assert "絵文字" in f and "半角カタカナ" in f
    assert "説明文: 1 本。最少 2 本" in f
    assert "パス2 だけを入れることはできない" in f
    refs = {(c["メモ"], c["行"]) for c in out["根拠"]}
    assert ("15", "1-03") in refs and ("15", "1-01") in refs
    assert all("読めなかった" in c["確かめ"] for c in out["根拠"])  # 知識ベースが無くても動く


def test_空白と句読点の目印():
    flags = specs.editorial_flags(" 今 だ け　激安、。")
    joined = " ".join(flags)
    assert "先頭か末尾に空白" in joined and "全角スペース" in joined and "1 文字ずつ空白" in joined
    assert "句読点・記号の連続「、。」" in joined
    assert specs.editorial_flags("観戦ガイド。ルールを 3 分で") == []


def test_公式の中で食い違う上限は両方で判定する():
    # DG の見出しは 2026-11-18 の管理画面での確認で半角 40 に決まった（15 の 1-56 ✅）。食い違いの判定はしない
    out = specs.ad_text_check("デマンド ジェネレーション", {"見出し": ["あ" * 17], "説明文": ["x"], "ビジネスの名前": "OCHINANA"})
    assert out["行"][0]["上限"] == 40 and out["行"][0]["判定"] == "収まる"
    f = " ".join(out["所見"])
    assert "半角 30 以内の文が 1 本も無い" in f and "1-56" not in f and "食い違う" not in f
    over = specs.ad_text_check("dg", {"見出し": ["あ" * 21]})
    assert over["行"][0]["判定"].startswith("超過")
    rda = specs.ad_text_check("rda", {"見出し": ["a"], "長い見出し": ["b"], "説明文": ["c" * 85]})
    assert rda["行"][0]["項目"] == "短い見出し"  # RDA の「見出し」は短い見出しとして数える
    assert rda["行"][2]["判定"].startswith("90 なら収まるが 80 だと超過")


def test_サイトリンクとP_MAX():
    out = specs.ad_text_check("サイトリンク", {"リンク文": ["ルール"], "説明文1": ["a"], "説明文2": [""]})
    f = " ".join(out["所見"])
    assert "片方だけは不可" in f and "2 個以上ないと出ない" in f
    pm = specs.ad_text_check("pmax", {"見出し": ["a" * 20, "b" * 20, "c" * 20], "説明文": ["d", "e"], "短い説明文": ["f"],
                                      "長い見出し": ["g"], "ビジネスの名前": ["OCHINANA"]})
    f = " ".join(pm["所見"])
    assert "半角 15 以内の文が 1 本も無い" in f and "未確認" in f
    with pytest.raises(ValueError, match="種類"):
        specs.ad_text_check("テレビ", {"見出し": ["a"]})
    with pytest.raises(ValueError):
        specs.ad_text_check("rsa", {})
    only = specs.ad_text_check("数えるだけ", {"文": ["あいう", "abc"]})
    assert [r["文字数（半角換算）"] for r in only["行"]] == [6, 3]


# ---------------- url_builder ----------------

def test_URLを組み立てる():
    out = urltool.build("https://example.com/guide/", utm={"source": "google", "utm_medium": "cpc", "campaign": "秋"},
                        suffix="kw={keyword}")
    assert out["最終ページ URL のサフィックス（入力欄に入れる）"] == "kw={keyword}&utm_source=google&utm_medium=cpc&utm_campaign=%E7%A7%8B"
    assert out["着地 URL の見本"].startswith("https://example.com/guide/?kw={keyword}&utm_source=google")
    assert out["問題"] is None
    assert any("24-05" in n for n in out["注記"])  # GA4 では自動タグ設定が UTM より優先


def test_URLの問題を知らせる():
    out = urltool.build("https://example.com/ホッケー?a=1#top", utm={"source": "x"}, suffix="?a=2&gclid=abc&x={_myparam}&y={_toolongparametername}")
    p = " ".join(out["問題"])
    assert "5-12" in p  # ? で始めない
    assert "パラメータ a が 2 回" in p
    assert "gclid を手で入れている" in p
    assert "# がある" in p and out["着地 URL の見本"].endswith("#top") and "?a=1&a=2" in out["着地 URL の見本"]
    assert "toolongparametername" in p
    assert any("エンコードしていない文字" in n and "%E3%83%9B" in n for n in out["注記"])
    with pytest.raises(ValueError):
        urltool.build("")
    bad = urltool.build("ftp://x")
    assert "5-18" in " ".join(bad["問題"])


def test_URLを分解する():
    out = urltool.parse("https://bit.ly/abc?utm_source=a&utm_source=b&gclid=1")
    assert out["部品"]["ホスト"] == "bit.ly"
    assert [p["名前"] for p in out["パラメータ"]] == ["utm_source", "utm_source", "gclid"]
    assert "utm_source" in out["問題"][0]
    n = " ".join(out["注記"])
    assert "短縮 URL" in n and "gclid が入っている" in n


# ---------------- unit_economics ----------------

def test_採算_37の例と同じ値():
    # 37 の 1-3: 30,000 円 × 40% ＝ 12,000、× 3 回 ＝ 36,000、1/3 ＝ 12,000、× 0.8 ＝ 9,600、× 0.3 ＝ 2,880。ROAS の下限 7.5
    out = calc.unit_economics(sale_price_yen=30000, gross_margin_rate=0.4, purchases_per_customer=3, ltv_months=12,
                              spend_share=1 / 3, incremental_rate=0.8, close_rate=0.3, cpc_yen=120)
    assert out["1 件の粗利"] == 12000 and out["生涯の粗利（LTV）"] == 36000
    assert out["許容できる獲得単価"] == {**out["許容できる獲得単価"], "成約 1 件": 9600, "メインの成果 1 件": 2880}
    assert out["損益分岐の獲得単価"]["成約 1 件"] == 28800  # 36,000 × 0.8
    assert out["目標広告費用対効果の下限"]["初回の売上で値を付けるとき"]["倍"] == 7.5
    assert out["目標広告費用対効果の下限"]["LTV で値を付けるとき"]["倍"] == 2.5
    assert out["回収期間"]["1 か月あたりの粗利（1 人）"] == 3000 and out["回収期間"]["許容の単価で獲得したとき（月）"] == 3.2
    assert out["逆算"]["必要なコンバージョン率"]["値"] == pytest.approx(120 / 2880, abs=1e-5)
    assert any("上限であって目標ではない" in n for n in out["注記"])


def test_採算_入力の確かめと逆算():
    out = calc.unit_economics(gross_profit_yen=5000, cpc_yen=100, cvr=0.02)
    assert out["損益分岐の獲得単価"]["成約 1 件"] == 5000
    assert out["逆算"]["このクリック単価と率での獲得単価"]["値"] == 5000
    assert out["逆算"]["この率で払えるクリック単価の上限"]["値"] == 100
    assert isinstance(out["目標広告費用対効果の下限"], str)  # 粗利率が分からない
    with pytest.raises(ValueError):
        calc.unit_economics()
    with pytest.raises(ValueError, match="粗利率"):
        calc.unit_economics(sale_price_yen=1000, gross_margin_rate=40)


# ---------------- budget_pace ----------------

def test_予算のペース_36の例と同じ値():
    # 36 の 7-2: 30 日の月の 16 日朝に累計 14 万円 → (300,000 − 140,000) ÷ 15 ＝ 10,667 円。基本の日予算 9,868 円
    out = calc.budget_pace(300000, 140000, 15, days_in_month=30, current_daily_budget_yen=12000)
    assert out["日予算の案"]["残りの日の目標（1 日あたり）"] == 10667
    assert out["日予算の案"]["この日予算での 1 日の上限（2 倍）"] == 21333
    assert out["基本の日予算（月の初めから置くなら）"]["値"] == 9868
    assert out["ペース（%）"] == 93.3 and out["判定の帯"]["判定"] == "健全"
    assert out["月末の着地の予測（いまのペースのまま）"] == 280000
    assert out["いまの日予算のままなら"]["月の上限（30.4 倍。月の初めから同じ日予算のとき）"] == 364800
    assert "超えて使われうる" in out["いまの日予算のままなら"]["読み方"]


def test_予算のペース_帯と境目():
    assert calc.budget_pace(300000, 100000, 20, days_in_month=30)["判定の帯"]["判定"] == "健全"   # 100%
    assert calc.budget_pace(300000, 85000, 20, days_in_month=30)["判定の帯"]["判定"] == "注意"    # 85%
    assert calc.budget_pace(300000, 130000, 20, days_in_month=30)["判定の帯"]["判定"] == "危険"   # 130%
    start = calc.budget_pace(300000, 0, 30, days_in_month=30)
    assert start["ペース（%）"] is None and start["日予算の案"]["残りの日の目標（1 日あたり）"] == 10000
    assert calc.budget_pace(100, 0, 1, month="2026-02")["期間"].startswith("28 日の月")
    over = calc.budget_pace(1000, 1200, 5, days_in_month=30)
    assert "超えている" in over["注記_使いすぎ"] and over["日予算の案"]["残りの日の目標（1 日あたり）"] == 0
    with pytest.raises(ValueError):
        calc.budget_pace(1000, 0, 40, days_in_month=30)
    with pytest.raises(ValueError):
        calc.budget_pace(0, 0, 3)


# ---------------- image_spec_check ----------------

def test_画像_比率と寸法と容量と形式():
    out = specs.image_spec_check(1200, 628, 300, "jpg")
    fits = {(r["フォーマット"], r["枠"]) for r in out["合う枠"]}
    assert ("レスポンシブ ディスプレイ広告", "横長画像") in fits and ("P-MAX", "横長画像") in fits
    assert ("デマンド ジェネレーション", "横長画像") in fits
    assert out["イメージ広告（アップロード）"]["判定"] == "合わない"
    small = specs.image_spec_check(500, 262)
    assert all(r["判定"] == "合わない" for r in small["比率は合うが条件を満たさない枠"] if r["枠"] != "横長画像（任意）")
    heavy = specs.image_spec_check(1200, 1200, 6000, "png", formats=["pmax"])
    assert {r["フォーマット"] for r in heavy["比率は合うが条件を満たさない枠"]} == {"P-MAX"}
    assert "容量が大きすぎる" in heavy["比率は合うが条件を満たさない枠"][0]["理由"][0]
    banner = specs.image_spec_check(300, 250, 120, "gif")
    assert banner["イメージ広告（アップロード）"]["判定"] == "合う" and banner["イメージ広告（アップロード）"]["枠"] == "スクエア・長方形"
    assert specs.image_spec_check(300, 250, 151, "png")["イメージ広告（アップロード）"]["判定"] == "合わない"
    gif = specs.image_spec_check(1200, 628, 100, "gif", formats=["P-MAX"])
    assert "食い違う" in " ".join(gif["合う枠"][0]["注記"])
    logo = specs.image_spec_check(130, 130, 100, formats=["dg"])
    row = next(r for r in logo["合う枠"] if r["枠"] == "ロゴ")
    assert "144" in " ".join(row["注記"])  # 128 以上 144 未満は食い違いを知らせる
    with pytest.raises(ValueError):
        specs.image_spec_check()
    with pytest.raises(ValueError, match="フォーマット"):
        specs.image_spec_check(10, 10, formats=["テレビ"])


def _png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00" + b"\x00" * 20


def _jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def test_画像のファイルを測る_Pillowが無くても(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_png(1200, 1200))
    out = specs.image_spec_check(path=str(p))
    assert out["測った結果"]["幅"] == 1200 and out["測った結果"]["形式"] == "PNG"
    assert any(r["枠"] == "スクエア画像" for r in out["合う枠"])
    j = tmp_path / "b.jpg"
    j.write_bytes(_jpeg(728, 90))
    m = specs.measure(str(j))
    assert (m["幅"], m["高さ"], m["形式"]) == (728, 90, "JPEG")
    g = tmp_path / "c.gif"
    g.write_bytes(b"GIF89a" + struct.pack("<HH", 320, 50) + b"\x00" * 10)
    assert specs.image_spec_check(path=str(g))["イメージ広告（アップロード）"]["枠"] == "モバイル"
    (tmp_path / "x.txt").write_text("no image")
    with pytest.raises(ValueError, match="読めなかった"):
        specs.measure(str(tmp_path / "x.txt"))
    with pytest.raises(ValueError, match="見つからない"):
        specs.measure(str(tmp_path / "none.png"))


# ---------------- significance ----------------

def test_有意性_手計算と照らす():
    lo, hi = stats.wilson_interval(20, 1000)
    assert lo == pytest.approx(0.01298, abs=1e-4) and hi == pytest.approx(0.03069, abs=1e-4)
    r = stats.significance("率の幅", success=20, total=1000)
    assert "2.00%" in r["一文"] and "1.30%〜3.07%" in r["一文"]
    d = stats.significance("2案の差", a_success=20, a_total=1000, b_success=30, b_total=1000)
    assert d["p 値"] == pytest.approx(0.152, abs=0.002) and d["差があると言えるか"] is False  # z = 1.43
    assert "差が無いという意味ではない" in d["一文"]
    big = stats.significance("2案の差", a_success=100, a_total=1000, b_success=150, b_total=1000)
    assert big["差があると言えるか"] is True
    small = stats.significance("2案の差", a_success=0, a_total=8, b_success=3, b_total=8)
    assert small["方法"].startswith("フィッシャー") and small["p 値"] == pytest.approx(0.2, abs=1e-6)
    n = stats.significance("必要な件数", baseline_rate=0.02, target_rate=0.03)
    assert n["1 案あたりに必要な件数"] == 3823  # data_needed と同じ式
    z = stats.significance("0件の上限", total=300)
    assert z["上限（3 の法則）"] == 0.01 and z["上限（正確）"] == pytest.approx(0.00994, abs=1e-4)


def test_件数の振れ幅_ポアソン():
    lo, hi = stats.poisson_interval(0)
    assert lo == 0 and hi == pytest.approx(3.689, abs=1e-3)
    lo, hi = stats.poisson_interval(10)
    assert lo == pytest.approx(4.795, abs=1e-3) and hi == pytest.approx(18.390, abs=1e-3)
    assert stats.poisson_range(10) == (4, 17)
    out = stats.significance("件数の振れ幅", count=3, expected=10)
    assert out["いつもの平均との比べ"]["範囲の中か"] is False and "偶然の範囲の外" in out["一文"]
    only = stats.significance("件数の振れ幅", expected=10)
    assert only["偶然で出る範囲"] == [4, 17]
    with pytest.raises(ValueError, match="mode"):
        stats.significance("なにか")
    with pytest.raises(ValueError):
        stats.significance("件数の振れ幅")


# ---------------- list_compare ----------------

def test_一覧の差分_表記ゆれをそろえる():
    out = calc.list_compare(["ホッケー ルール", "ＡＢＣ", "abc", "観戦"], ["ホッケールール", "def"], label_a="キーワード", label_b="検索語句")
    assert out["件数"]["両方にある"] == 1 and out["件数"]["キーワード だけ"] == 2 and out["件数"]["検索語句 だけ"] == 1
    assert out["両方にある"][0]["キーワード の表記"] == ["ホッケー ルール"]
    assert out["それぞれの中の重複（そろえると同じになるもの）"]["キーワード"] == [["abc", "ＡＢＣ"]]
    strict = calc.list_compare(["ホッケー ルール"], ["ホッケールール"], ignore_space=False)
    assert strict["件数"]["両方にある"] == 0
    marks = calc.list_compare(["[ホッケー]"], ['"ホッケー"'], ignore_match_marks=True)
    assert marks["件数"]["両方にある"] == 1
    assert calc.list_compare("a、b\nc", ["c"])["件数"]["A"] == 3


def test_除外がキーワードを止めていないか():
    out = calc.list_compare(["ホッケー ルール", "[ホッケー 初心者]", '"ホッケー 試合"', "ホッケー 観戦"],
                            ["-ルール ホッケー", '"初心者 ホッケー"', "[ホッケー 試合]", "[ホッケー]"], mode="除外の点検")
    got = {(h["キーワード"], h["止めている除外キーワード"]) for h in out["止められているキーワード"]}
    assert got == {("ホッケー ルール", "-ルール ホッケー"),    # 部分一致の除外は語順を問わない
                   ('"ホッケー 試合"', "[ホッケー 試合]")}     # 完全一致の除外は同じ並びだけ
    # フレーズ一致の除外は語順が違うと止まらない（15 の 3-09）、[ホッケー] は 1 語の検索だけ
    assert out["件数"]["止められているキーワード"] == 2
    with pytest.raises(ValueError, match="mode"):
        calc.list_compare(["a"], ["b"], mode="合体")


# ---------------- feed_check ----------------

GOOD = {"id": "A1", "title": "ホッケー スティック 初心者用 軽量", "description": "練習用", "image_link": "https://example.com/a.jpg",
        "price": "1980 JPY", "availability": "in_stock", "brand": "OCHI", "gtin": "4901234567894"}


def test_フィード_問題の無い行():
    out = feed.feed_check(GOOD)
    assert out["件数"]["所見"] == 0 and out["行"] == []


def test_フィード_必須と字数と書式():
    bad = {**GOOD, "id": "x" * 51, "price": "1,980円", "availability": "instock", "gtin": "4901234567890", "brand": "",
           "title": "送料無料 ホッケー スティック", "color": "#ff0000", "sale_price": "980.123 JPY",
           "availability_date": "", "custom_label_0": "y" * 101, "pause": "forever"}
    del bad["description"]
    out = feed.feed_check(bad)
    got = {(i["属性"], i["種類"]) for i in out["行"][0]["所見"]}
    assert ("description", "必須の欠け") in got and ("id", "字数") in got and ("custom_label_0", "字数") in got
    assert ("price", "書式") in got and ("sale_price", "書式") in got
    assert ("availability", "値") in got and ("gtin", "値") in got and ("color", "値") in got
    assert ("brand", "必須の欠け") in got and ("title", "書き方") in got and ("pause", "値") in got
    pre = feed.feed_check({**GOOD, "availability": "preorder"})
    assert ("availability_date", "必須の欠け") in {(i["属性"], i["種類"]) for i in pre["行"][0]["所見"]}
    no_id = feed.feed_check({**GOOD, "gtin": ""})
    assert ("mpn", "必須の欠け") in {(i["属性"], i["種類"]) for i in no_id["行"][0]["所見"]}
    assert feed.feed_check({**GOOD, "gtin": "", "identifier_exists": "no"})["件数"]["所見"] == 0
    assert any("link" in x for x in out["点検していない決まり"])


def test_フィード_CSVとTSV(tmp_path):
    p = tmp_path / "feed.tsv"
    p.write_text("id\ttitle\tdescription\timage_link\tprice\tavailability\tbrand\tgtin\tmy_col\n"
                 "A1\tホッケー\t説明\thttps://e/a.jpg\t1980 JPY\tin_stock\tX\t4901234567894\tz\n"
                 "A2\tホッケー\t説明\thttps://e/b.jpg\t0 JPY\tsold\tX\t4901234567894\tz\n", encoding="utf-8")
    out = feed.feed_check(csv_path=str(p))
    assert out["入力"]["行数"] == 2 and out["件数"]["所見のある行"] == 1 and out["行"][0]["行"] == 2
    assert out["知識ベースの表に無い属性（点検していない）"] == ["my_col"]
    c = tmp_path / "feed.csv"
    c.write_bytes("id,title,description,image_link,price,availability,brand,gtin\nA1,ホッケー,説明,u,1980 JPY,in_stock,X,4901234567894\n".encode("cp932"))
    assert feed.feed_check(csv_path=str(c))["件数"]["所見"] == 0  # Shift_JIS も読む
    with pytest.raises(ValueError):
        feed.feed_check()
    with pytest.raises(ValueError):
        feed.feed_check(GOOD, csv_path=str(c))


# ---------------- サーバーへの登録 ----------------

def test_サーバーに道具と使い方の決まりが入っている():
    tools = asyncio.run(server.server.list_tools())
    names = {t.name for t in tools}
    new = {"kb_overview", "kb_search", "kb_read", "kb_rules", "kb_logic_tree", "kb_glossary", "kb_tips", "kb_check_claim",
           "ad_text_check", "url_builder", "unit_economics", "budget_pace", "image_spec_check", "significance",
           "list_compare", "feed_check", "kb_kata", "kb_quiz", "kb_video_guide", "video_storyboard_check", "video_spec_check", "cpa_roas_simulate", "media_plan_simulate", "report_number_check", "report_numbers_csv"}
    assert new <= names and len(names) == 56 and {"meta_list_accounts", "meta_get_performance", "meta_get_daily", "report_numbers", "report_weekly_self", "report_to_html", "report_sheet_plan", "report_sheet_check"} <= names
    for t in tools:
        if t.name in new:
            assert t.annotations.read_only_hint is True and t.annotations.open_world_hint is False
    ins = server.server.instructions
    assert "答える前に必ず知識ベースを引く" in ins and "未確認" in ins and "仮説" in ins
    assert "kb_read(file='90')" in ins and "kb_kata" in ins and "〔推論〕" in ins and "30 日" in ins
    assert "計測 → 成果の定義 → 絞る → 素材 → 構成 → 入札・予算 → 広げる" in ins


# ---------------- 実物の知識ベースと突き合わせる（あるときだけ） ----------------

REAL = Path(kb.DEFAULT_KB_DIR)


@pytest.mark.skipif(not REAL.is_dir(), reason="実物の知識ベースが無い環境")
def test_実物_道具の根拠の行がいまも知識ベースにある(monkeypatch):
    """道具の中に置いた数字の根拠が、いまの知識ベースの行と合うか。合わなければメモが直されたので、道具の値を見直す。"""
    monkeypatch.setenv(kb.ENV_KB_DIR, str(REAL))
    outs = [specs.ad_text_check(name, {f: ["a"] for f in spec["項目"]}) for name, spec in specs.AD_SPECS.items()]
    outs.append(specs.image_spec_check(1200, 1200, 100, "png"))
    outs.append(specs.image_spec_check(1200, 628, 100, "png"))
    outs.append(specs.image_spec_check(600, 1067, 100, "png"))
    outs.append(specs.image_spec_check(1200, 1500, 100, "png"))
    outs.append(specs.image_spec_check(1200, 300, 100, "png"))
    outs.append(specs.image_spec_check(300, 250, 100, "png"))
    outs.append(calc.unit_economics(sale_price_yen=1000, gross_margin_rate=0.5))
    outs.append(calc.budget_pace(1000, 100, 10, days_in_month=30))
    outs.append(calc.list_compare(["a"], ["b"], mode="除外の点検"))
    outs.append(urltool.build("https://example.com/#x", utm={"source": "a"}))
    bad_row = {"title": "<b>SALE SALE</b> 送料無料", "price": "x", "availability": "x", "gtin": "1", "color": "1",
               "sale_price": "1", "pause": "x", "custom_label_0": "x" * 200, "promotion_id": ",".join(["p"] * 11),
               "product_highlight": "a", "additional_image_link": " ".join(["u"] * 11), "identifier_exists": "x",
               "condition": "x", "id": "x" * 60}
    outs.append(feed.feed_check(bad_row))
    text = json.dumps(outs, ensure_ascii=False)
    cites = []

    def walk(o):
        if isinstance(o, dict):
            if "確かめ" in o:
                cites.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(outs)
    assert len(cites) > 40
    broken = [c for c in cites if "見つからない" in c["確かめ"] or "見当たらない" in c["確かめ"] or "読めなかった" in c["確かめ"]]
    assert broken == [], json.dumps(broken, ensure_ascii=False, indent=1)
    assert "読めなかった" not in text
    # ValueTrack の一覧は 15 の 5 章から読める
    rules, err = urltool._valuetrack_rules()
    assert err is None and {"lpurl", "keyword", "campaignid", "gclid", "ignore"} <= set(rules)
