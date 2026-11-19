"""LP の監査。外へはつながず、にせの応答で試す。"""
import json
import urllib.parse
from pathlib import Path

import pytest

from ochinana_ads import config, lp

HTML = """<!doctype html><html lang="ja"><head>
<title> はじめての6人制ホッケー｜観戦ガイド </title>
<meta name="description" content="ルールと観戦の見方を図解で案内。">
<link rel="canonical" href="https://example.com/">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ"></script>
<script>gtag('config','G-ABC123XYZ');</script>
</head><body>
<h1>はじめての<br>6人制ホッケー</h1><h2>見出し2</h2></body></html>"""

PSI = {"lighthouseResult": {
    "lighthouseVersion": "13.0.1",
    "categories": {"performance": {"score": 0.74}},
    "audits": {
        "first-contentful-paint": {"numericValue": 2810.4},
        "largest-contentful-paint": {"numericValue": 5120.0},
        "total-blocking-time": {"numericValue": 140.2},
        "cumulative-layout-shift": {"numericValue": 0.0031},
        "render-blocking-resources": {"details": {"items": [
            {"url": "https://fonts.googleapis.com/css2?family=Noto+Sans+JP", "wastedMs": 1800, "totalBytes": 2048},
            {"url": "https://example.com/a.css", "wastedMs": 300, "totalBytes": 10240},
        ]}},
    },
}}


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    monkeypatch.delenv(lp.ENV_LIGHTHOUSE_BIN, raising=False)
    monkeypatch.setattr(lp, "find_lighthouse", lambda: None)  # この Mac に入っているかどうかに左右されないように
    monkeypatch.setattr(lp, "_psi_paused_until", 0.0)


def fake_lighthouse(score=0.61, calls=None):
    def run(url, out_path):
        if calls is not None:
            calls.append(url)
        raw = json.loads(json.dumps(PSI["lighthouseResult"]))
        raw["categories"]["performance"]["score"] = score
        out_path.write_text(json.dumps(raw), encoding="utf-8")
        return {"lighthouseResult": raw}
    return run


def fake_get(psi_status=200, page_status=200, psi_body=None, calls=None):
    def get(url, timeout):
        if calls is not None:
            calls.append(url)
        if url.startswith(lp.PSI_ENDPOINT):
            return psi_status, url, json.dumps(psi_body or PSI).encode()
        return page_status, url, HTML.encode()
    return get


@pytest.mark.parametrize("bad", ["ftp://example.com/", "example.com", "javascript:alert(1)", "https://user:pw@example.com/",
                                 "http://localhost:3000/", "http://127.0.0.1/", "http://192.168.1.10/x", "http://printer.local/", ""])
def test_受けないURL(bad):
    with pytest.raises(lp.LpError):
        lp.check_url(bad)


def test_受けるURL():
    assert lp.check_url(" https://handball-teams-guide.example.com/teams/ ") == "https://handball-teams-guide.example.com/teams/"


def test_計測の依頼に付けるのはURLと条件だけ():
    u = urllib.parse.urlsplit(lp.psi_request_url("https://example.com/a?b=1"))
    q = urllib.parse.parse_qs(u.query)
    assert set(q) == {"url", "strategy", "category"}
    assert q["url"] == ["https://example.com/a?b=1"] and q["strategy"] == ["mobile"]
    assert "key" not in q


def test_表示速度の要点を取り出す():
    s = lp.summarize_psi(PSI)
    assert s["総合点（100 点満点）"] == 74
    assert s["最初の表示（秒）"] == 2.8
    assert s["メイン画像の表示（秒）"] == 5.1
    assert s["操作を受け付けない時間の合計（ミリ秒）"] == 140
    assert s["レイアウトのずれ"] == 0.003
    top = s["描画を止めている資源（上位）"]
    assert top[0] == {"資源": "https://fonts.googleapis.com/css2", "止めている時間（ミリ秒）": 1800, "大きさ（KB）": 2.0}
    assert len(top) == 2


def test_新しい版の項目名でも読める():
    raw = {"lighthouseResult": {"categories": {"performance": {"score": 0.9}}, "audits": {
        "render-blocking-insight": {"details": {"items": [{"url": "https://example.com/x.css", "wastedMs": 120}]}}}}}
    assert lp.summarize_psi(raw)["描画を止めている資源（上位）"][0]["資源"] == "https://example.com/x.css"


def test_点数が無い結果は失敗にする():
    with pytest.raises(lp.LpError):
        lp.summarize_psi({"error": {"code": 500}})


def test_ページの要素とタグ():
    p = lp.parse_page(HTML)
    assert p["title"] == "はじめての6人制ホッケー｜観戦ガイド"
    assert p["meta description"] == "ルールと観戦の見方を図解で案内。"
    assert p["h1"] == ["はじめての 6人制ホッケー"] or p["h1"] == ["はじめての6人制ホッケー"]
    assert p["h1 の数"] == 1
    assert p["canonical"] == "https://example.com/"
    assert p["言語の指定"] == "ja"
    assert p["Google タグ"]["有無"] is True and p["Google タグ"]["ID"] == ["G-ABC123XYZ"]


def test_タグが無いページ():
    p = lp.parse_page("<html><head><title>x</title></head><body><p>本文</p></body></html>")
    assert p["Google タグ"]["有無"] is False
    assert p["meta description"] is None and p["h1"] == [] and p["canonical"] is None


def test_監査は要点だけ返し生データは保存する(tmp_path):
    calls = []
    out = lp.audit("https://example.com/", get=fake_get(calls=calls))
    assert out["表示速度（スマホ）"]["測れたか"] is True
    assert out["表示速度（スマホ）"]["測り方"] == "PageSpeed Insights"
    assert out["表示速度（スマホ）"]["総合点（100 点満点）"] == 74
    assert out["ページの要素"]["取れたか"] is True
    saved = out["生データの保存先"]
    assert set(saved) == {"表示速度の生データ", "ページの HTML"}
    assert all(str(tmp_path) in p for p in saved.values())
    assert "lighthouseResult" not in json.dumps(out)  # 生の結果は返答に入れない
    assert len(calls) == 2  # 計測 1 回、ページの取得 1 回


def test_両方だめなら測れなかったと返し_ページの要素は返す():
    out = lp.audit("https://example.com/", get=fake_get(psi_status=429))
    speed = out["表示速度（スマホ）"]
    assert speed["測れたか"] is False
    assert "PageSpeed Insights: HTTP 429" in speed["理由"] and "上限" in speed["理由"]
    assert "手元の Lighthouse: 手元に Lighthouse が入っていない" in speed["理由"]
    assert out["ページの要素"]["取れたか"] is True
    assert "表示速度の生データ" not in (out["生データの保存先"] or {})


def test_PageSpeedが断られたら手元のLighthouseで測る(tmp_path):
    used = []
    out = lp.audit("https://example.com/", get=fake_get(psi_status=429), lighthouse=fake_lighthouse(0.61, used))
    speed = out["表示速度（スマホ）"]
    assert speed["測れたか"] is True
    assert speed["測り方"] == "手元の Lighthouse"
    assert "429" in speed["PageSpeed Insights を使わなかった理由"]
    assert speed["総合点（100 点満点）"] == 61 and speed["メイン画像の表示（秒）"] == 5.1
    assert "この Mac" in speed["計測の条件"]
    assert used == ["https://example.com/"]
    assert Path(out["生データの保存先"]["表示速度の生データ"]).name.startswith("lighthouse_")


def test_PageSpeedで測れたら手元のLighthouseは動かさない():
    used = []
    out = lp.audit("https://example.com/", get=fake_get(), lighthouse=fake_lighthouse(calls=used))
    assert out["表示速度（スマホ）"]["測り方"] == "PageSpeed Insights" and used == []


def test_上限で断られたあとはしばらくPageSpeedを呼ばない():
    calls = []
    lp.audit("https://example.com/a", get=fake_get(psi_status=429, calls=calls), lighthouse=fake_lighthouse())
    out = lp.audit("https://example.com/b", get=fake_get(psi_status=429, calls=calls), lighthouse=fake_lighthouse())
    assert len([c for c in calls if c.startswith(lp.PSI_ENDPOINT)]) == 1
    assert "今回は呼んでいない" in out["表示速度（スマホ）"]["PageSpeed Insights を使わなかった理由"]
    assert out["表示速度（スマホ）"]["測り方"] == "手元の Lighthouse"


def test_手元のLighthouseの結果も使い回せる():
    used = []
    lp.audit("https://example.com/", get=fake_get(psi_status=429), lighthouse=fake_lighthouse(calls=used))
    out = lp.audit("https://example.com/", get=fake_get(psi_status=429), lighthouse=fake_lighthouse(calls=used), max_age_hours=6)
    assert len(used) == 1
    assert out["表示速度（スマホ）"]["測り方"] == "手元の Lighthouse" and "再利用" in out["表示速度（スマホ）"]["計測の条件"]


def test_Lighthouseに渡すのはURLと測り方の指定だけ(tmp_path):
    cmd = lp.lighthouse_command("/x/lighthouse", "https://example.com/", tmp_path / "o.json")
    assert cmd[:2] == ["/x/lighthouse", "https://example.com/"]
    assert "--only-categories=performance" in cmd and "--form-factor=mobile" in cmd and "--output=json" in cmd
    assert "--chrome-flags=--headless=new" in cmd
    assert not any(a in ("-y", "--yes", "install") for a in cmd)  # その場で取りに行く指定は付けない


def test_手元のLighthouseを探す順番(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path))
    fake = tmp_path / "lighthouse"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv(lp.ENV_LIGHTHOUSE_BIN, str(fake))
    assert lp.find_lighthouse() == str(fake)
    monkeypatch.setenv(lp.ENV_LIGHTHOUSE_BIN, str(tmp_path / "none"))
    assert lp.find_lighthouse() is None


def _fake_binary(tmp_path, body):
    script = tmp_path / "lighthouse"
    script.write_text("#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --output-path=*) OUT=\"${a#--output-path=}\";; esac; done\n" + body)
    script.chmod(0o755)
    return script


def test_手元のLighthouseを実際に起動して結果を読む(tmp_path, monkeypatch):
    """Lighthouse の代わりに、同じ引数を受けて結果のファイルを書く小さなスクリプトを置いて試す。"""
    result = json.dumps(PSI["lighthouseResult"]).replace("'", "")
    script = _fake_binary(tmp_path, f"printf '%s' '{result}' > \"$OUT\"\n")
    monkeypatch.setattr(lp, "find_lighthouse", lambda: str(script))
    raw = lp.run_lighthouse("https://example.com/", tmp_path / "out.json")
    assert lp.summarize_psi(raw)["総合点（100 点満点）"] == 74


def test_手元のLighthouseが失敗したら理由を返す(tmp_path, monkeypatch):
    script = _fake_binary(tmp_path, "echo 'Chrome が見つからない' >&2\nexit 3\n")
    monkeypatch.setattr(lp, "find_lighthouse", lambda: str(script))
    with pytest.raises(lp.LpError, match="終了コード 3"):
        lp.run_lighthouse("https://example.com/", tmp_path / "out.json")
    script = _fake_binary(tmp_path, "printf '%s' '{\"runtimeError\": {\"code\": \"NO_FCP\"}}' > \"$OUT\"\n")
    with pytest.raises(lp.LpError, match="NO_FCP"):
        lp.run_lighthouse("https://example.com/", tmp_path / "out.json")


def test_ページが無いときは理由を返す():
    out = lp.audit("https://example.com/none", get=fake_get(page_status=404))
    assert out["ページの要素"] == {"取れたか": False, "理由": "HTTP 404。ページが公開されていないか、URL が違う"}
    assert out["表示速度（スマホ）"]["測れたか"] is True


def test_少し前の計測を使い回せる():
    calls = []
    lp.audit("https://example.com/", get=fake_get(calls=calls))
    out = lp.audit("https://example.com/", get=fake_get(calls=calls), max_age_hours=6)
    psi_calls = [c for c in calls if c.startswith(lp.PSI_ENDPOINT)]
    assert len(psi_calls) == 1
    assert "再利用" in out["表示速度（スマホ）"]["計測の条件"]
