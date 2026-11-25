"""Meta 広告を読む道具（meta_*）。本物の API にはつながず、模擬の応答で確かめる。ID・名前は架空。"""
import argparse
import inspect
import json
import os
import re
import urllib.parse

import pytest

from ochinana_ads import connect, meta, tools

TOKEN = "EAAfakeTokenValueShouldNeverAppear0123456789"
A1, A2, OTHER = "1111111111", "2222222222", "9999999999"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    f = tmp_path / "meta_token.txt"
    f.write_text(TOKEN + "\n", encoding="utf-8")
    os.chmod(f, 0o600)
    monkeypatch.setenv(meta.ENV_TOKEN_FILE, str(f))
    monkeypatch.setenv(meta.ENV_ALLOWED, f"act_{A1}, {A2}")
    monkeypatch.setenv("OCHINANA_ADS_OUT_DIR", str(tmp_path / "out"))
    return tmp_path


class Fake:
    """GET の記録と、決めた応答を返す。method は Request を作らないので、URL とヘッダーだけ見る。"""

    def __init__(self, routes, headers=None):
        self.routes, self.calls, self.headers = routes, [], headers or {}

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        for key, resp in self.routes.items():
            if u.path.endswith(key) and (resp.get("_after") is None or q.get("after") == resp["_after"]):
                body = {k: v for k, v in resp.items() if k not in ("_after", "_status")}
                return resp.get("_status", 200), self.headers, json.dumps(body).encode()
        return 404, {}, json.dumps({"error": {"message": "not found", "code": 100}}).encode()


def client(routes, headers=None, slept=None):
    fake = Fake(routes, headers)
    c = meta.Client(fetch=fake, sleep=(slept.append if slept is not None else (lambda s: None)))
    return c, fake


def ins(**kw):
    base = {"account_currency": "JPY", "campaign_id": "c1", "campaign_name": "架空キャンペーン A", "objective": "OUTCOME_TRAFFIC",
            "optimization_goal": "LINK_CLICKS", "date_start": "2026-11-01", "date_stop": "2026-11-07", "impressions": "1000",
            "reach": "800", "clicks": "60", "inline_link_clicks": "40", "inline_link_click_ctr": "4.0",
            "cost_per_inline_link_click": "25.5", "cpm": "1020", "spend": "1020",
            "results": [{"indicator": "actions:link_click", "values": [{"value": "40"}]}],
            "cost_per_result": [{"indicator": "actions:link_click", "values": [{"value": "25.5"}]}]}
    base.update(kw)
    return base


# ---------------- 守り ----------------

def test_呼ぶのはGETだけで_書き込みの呼び方がコードに無い():
    src = inspect.getsource(meta)
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith(("#", '"', "-")))
    assert 'method="GET"' in src
    assert not re.search(r'method\s*=\s*"(POST|DELETE|PUT|PATCH)"', src)
    assert "urlopen(" in src and src.count("urlopen(") == 1  # 外へ出る口は _http_get の 1 つだけ
    assert not [n for n, _ in inspect.getmembers(meta.Client, inspect.isfunction) if n.lower().startswith(("post", "delete", "put"))]
    assert "/insights" in code  # 読む先は insights と広告アカウント


def test_許可リストに無いIDは_APIを呼ぶ前に断る(env):
    c, fake = client({})
    with pytest.raises(ValueError, match="許可リストに無い"):
        meta.performance(OTHER, "2026-11-01", "2026-11-07", client=c)
    assert fake.calls == []
    assert meta.require_allowed(f"act_{A1}") == A1 and meta.allowed_ids() == (A1, A2)


def test_許可リストが空なら_用意の仕方を返す(env, monkeypatch):
    monkeypatch.setenv(meta.ENV_ALLOWED, "")
    with pytest.raises(ValueError, match="connect check --media meta"):
        meta.performance(A1, "2026-11-01", "2026-11-07", client=client({})[0])


def test_鍵はヘッダーだけで送り_URLにも返り値にもCSVにも出ない(env):
    rows = [ins(campaign_name=f"架空 {i}", spend=str(100 + i)) for i in range(25)]
    c, fake = client({f"/act_{A1}/insights": {"data": rows}})
    out = meta.performance(A1, "2026-11-01", "2026-11-07", top_n=5, client=c)
    url, headers = fake.calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in url and "access_token" not in url
    assert url.startswith("https://graph.facebook.com/v")
    assert TOKEN not in json.dumps(out, ensure_ascii=False)
    csv_text = open(out["全件の CSV"], encoding="utf-8-sig").read()
    assert TOKEN not in csv_text and "架空 24" in csv_text


def test_APIのエラーの文に鍵が混じっても_出さない(env):
    err = {"error": {"message": f"Invalid OAuth access token {TOKEN}", "type": "OAuthException", "code": 190, "fbtrace_id": "abc"},
           "_status": 400}
    c, _ = client({f"/act_{A1}/insights": err})
    with pytest.raises(meta.MetaError) as ei:
        meta.performance(A1, "2026-11-01", "2026-11-07", client=c)
    text = str(ei.value)
    assert TOKEN not in text and "[鍵]" in text and "作り直し" in text and "code 190" in text


def test_通信の失敗の文にも鍵を出さない(env):
    def boom(url, headers):
        raise OSError(f"failed with {headers['Authorization']}")

    with pytest.raises(meta.MetaError) as ei:
        meta.Client(fetch=boom).get(f"/act_{A1}", {"fields": "name"})
    assert TOKEN not in str(ei.value) and ei.value.__cause__ is None


def test_鍵をURLに付ける呼び方は断る(env):
    with pytest.raises(ValueError, match="URL に付けない"):
        meta.Client(fetch=Fake({})).get(f"/act_{A1}", {"access_token": "x"})


def test_鍵のファイルの確かめは中身を開かない(env, monkeypatch, tmp_path):
    st = meta.token_file_status()
    assert st["ファイル"] == "ある" and TOKEN not in json.dumps(st, ensure_ascii=False)
    os.chmod(os.environ[meta.ENV_TOKEN_FILE], 0o644)
    assert "chmod 600" in json.dumps(meta.token_file_status(), ensure_ascii=False)
    monkeypatch.delenv(meta.ENV_TOKEN_FILE)
    assert "META_ADS_TOKEN_FILE" in meta.token_file_status()["直し方"]


def test_鍵のファイルはJSONの形でも読める(env):
    p = os.environ[meta.ENV_TOKEN_FILE]
    open(p, "w").write(json.dumps({"access_token": TOKEN}))
    c, fake = client({f"/act_{A1}": {"name": "架空の広告アカウント", "currency": "JPY", "account_status": 1}})
    meta.list_accounts(client=c)
    assert fake.calls[0][1]["Authorization"] == f"Bearer {TOKEN}"


# ---------------- 中身 ----------------

def test_期間の成果は_リンクの版の指標と結果と注記を返す(env):
    rows = [ins(), ins(campaign_name="架空キャンペーン B", spend="2000", impressions="3000", inline_link_clicks="60", clicks="90",
                   results=[{"indicator": "actions:link_click", "values": [{"value": "60"}]}])]
    c, fake = client({f"/act_{A1}/insights": {"data": rows}})
    out = meta.performance(A1, "2026-11-01", "2026-11-07", client=c)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(fake.calls[0][0]).query))
    assert q["level"] == "campaign" and json.loads(q["time_range"]) == {"since": "2026-11-01", "until": "2026-11-07"}
    assert "time_increment" not in q and "inline_link_clicks" in q["fields"] and "results" in q["fields"]
    r = out["行"][0]
    assert r["キャンペーン"] == "架空キャンペーン B"  # 消化金額の多い順
    assert r["リンククリック"] == 60 and r["クリック(すべて)"] == 90 and r["結果"] == 60.0 and r["結果の種類"] == "actions:link_click"
    t = out["合計"]
    assert t["消化金額"] == 3020 and t["リンククリック"] == 100 and t["CPC(リンク)"] == 30 and t["結果"] == 100.0
    assert t["CTR(リンク)（%）"] == 2.5 and t["CPM"] == 755
    for k in ("結果", "窓", "計上日", "まだ動く", "クリック"):
        assert k in out["注記"]
    assert "全件の CSV" not in out  # 行が少ないときは CSV を作らない


def test_数えている物が違う結果は_合計で足さない(env):
    rows = [ins(), ins(campaign_name="架空 C", results=[{"indicator": "actions:offsite_conversion.fb_pixel_purchase",
                                                         "values": [{"value": "3"}]}])]
    c, _ = client({f"/act_{A1}/insights": {"data": rows}})
    out = meta.performance(A1, "2026-11-01", "2026-11-07", client=c)
    assert str(out["合計"]["結果"]).startswith("足さない")
    assert meta.result_value("12") == (12.0, "")
    assert meta.result_value([{"indicator": "a", "values": [{"value": "1"}]}, {"indicator": "b", "values": [{"value": "2"}]}])[0] is None


def test_日別は_1日ごとの合計とCSVを返し_リーチは足し上げない(env):
    rows = [ins(date_start=d, date_stop=d, campaign_name=n, spend=s, impressions=i, inline_link_clicks=l)
            for d, n, s, i, l in [("2026-11-01", "架空 A", "100", "200", "10"), ("2026-11-01", "架空 B", "300", "600", "20"),
                                  ("2026-11-02", "架空 A", "50", "100", "5")]]
    c, fake = client({f"/act_{A1}/insights": {"data": rows}})
    out = meta.daily(A1, "2026-11-01", "2026-11-02", level="adset", client=c)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(fake.calls[0][0]).query))
    assert q["time_increment"] == "1" and q["level"] == "adset"
    days = out["日別の合計"]
    assert [d["日付"] for d in days] == ["2026-11-01", "2026-11-02"]
    assert days[0]["消化金額"] == 400 and days[0]["表示"] == 800 and "リーチ" not in days[0]
    csv_text = open(out["全件の CSV"], encoding="utf-8-sig").read()
    assert csv_text.count("\n") == 4 and "広告セット" in csv_text.splitlines()[0]


def test_ページはcursorsで送り_nextのURLは使わない(env):
    nxt = f"https://graph.facebook.com/v27.0/act_{A1}/insights?access_token={TOKEN}&after=CUR1"
    routes = {f"/act_{A1}/insights": {"data": [ins(campaign_name="架空 1")], "paging": {"cursors": {"after": "CUR1"}, "next": nxt}}}
    fake = Fake(routes)
    first = fake.routes[f"/act_{A1}/insights"]

    def fetch(url, headers):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if q.get("after") == "CUR1":
            fake.calls.append((url, headers))
            return 200, {}, json.dumps({"data": [ins(campaign_name="架空 2")], "paging": {"cursors": {"after": "CUR2"}}}).encode()
        return fake(url, headers)

    out = meta.performance(A1, "2026-11-01", "2026-11-07", client=meta.Client(fetch=fetch))
    assert {r["キャンペーン"] for r in out["行"]} == {"架空 1", "架空 2"}
    assert all(TOKEN not in u for u, _ in fake.calls) and first


def test_上限のヘッダーが80パーセントを超えたら待つ(env):
    h = {"X-FB-Ads-Insights-Throttle": json.dumps({"app_id_util_pct": 12, "acc_id_util_pct": 85, "ads_api_access_tier": "development_access"}),
         "X-Business-Use-Case-Usage": json.dumps({"123": [{"type": "ads_insights", "call_count": 30, "total_cputime": 10,
                                                           "total_time": 20, "estimated_time_to_regain_access": 0}]})}
    slept = []
    c, _ = client({f"/act_{A1}/insights": {"data": [ins()]}}, headers=h, slept=slept)
    out = meta.performance(A1, "2026-11-01", "2026-11-07", client=c)
    assert slept and slept[0] <= meta.MAX_WAIT_SECONDS
    assert out["呼び出しの上限"]["使った割合（%）"]["Insights の acc_id_util_pct"] == 85.0
    assert out["呼び出しの上限"]["待った秒数"] == slept[0]


def test_円以外のアカウントは注記する(env):
    c, _ = client({f"/act_{A1}/insights": {"data": [ins(account_currency="USD", spend="12.345")]}})
    out = meta.performance(A1, "2026-11-01", "2026-11-07", client=c)
    assert "USD" in out["注記"]["通貨"] and out["行"][0]["消化金額"] == 12.35


def test_levelと期間の誤りを断る(env):
    with pytest.raises(ValueError, match="level"):
        meta.performance(A1, "2026-11-01", "2026-11-07", level="account", client=client({})[0])
    with pytest.raises(ValueError, match="開始日"):
        meta.daily(A1, "2026-11-08", "2026-11-07", client=client({})[0])


def test_アカウントの一覧は許可リストの分だけ読む(env):
    c, fake = client({f"/act_{A1}": {"name": "架空の広告アカウント", "currency": "JPY", "timezone_name": "Asia/Tokyo", "account_status": 1},
                      f"/act_{A2}": {"error": {"message": "permission", "code": 200}, "_status": 403}})
    out = meta.list_accounts(client=c)
    rows = {r["ID"]: r for r in out["アカウント"]}
    assert rows[A1]["状態"] == "有効" and "ads_read" in rows[A2]["読めない理由"]
    assert all("/me/" not in u for u, _ in fake.calls)  # 一覧の道具は、許可リストの外を読む /me/adaccounts を呼ばない


# ---------------- 接続の確かめ ----------------

def test_check_connectionにMetaの段が付き_用意が無ければ何が要るかを返す(monkeypatch):
    monkeypatch.delenv(meta.ENV_TOKEN_FILE, raising=False)
    monkeypatch.delenv(meta.ENV_ALLOWED, raising=False)
    monkeypatch.delenv("GOOGLE_ADS_CONFIGURATION_FILE_PATH", raising=False)
    out = tools.check_connection(run=lambda *a: [], accessible=lambda: [])
    m = out["Meta 広告"]
    assert m["段"][0]["結果"] == "まだ" and "ads_read" in m["結論"] and "README" in m["結論"]


def test_check_connectionのMetaの段は_許可リストの広告アカウントを読む(env):
    c, _ = client({f"/act_{A1}": {"name": "架空", "currency": "JPY", "account_status": 1},
                   f"/act_{A2}": {"name": "架空 2", "currency": "JPY", "account_status": 1}})
    out = meta.check(client=c)
    assert out["結論"].startswith("Meta もつながった") and TOKEN not in json.dumps(out, ensure_ascii=False)


def test_端末のcheckは_鍵が無ければ用意を出し_あれば許可リストの1行を作る(env, monkeypatch, capsys):
    c, fake = client({"/me/adaccounts": {"data": [{"account_id": A1, "id": f"act_{A1}", "name": "架空 1", "currency": "JPY", "account_status": 1},
                                                  {"account_id": OTHER, "id": f"act_{OTHER}", "name": "架空 2", "currency": "USD", "account_status": 1}]}})
    res = connect.cmd_check(argparse.Namespace(media="meta", allow_pipe=True), client=c, ask=lambda _: "2")
    assert res["line"] == f"{meta.ENV_ALLOWED}={OTHER}" and "USD" in res["notes"][0]
    shown = capsys.readouterr().out
    assert "AI に貼らない" in shown and TOKEN not in shown
    monkeypatch.delenv(meta.ENV_TOKEN_FILE)
    assert connect.cmd_check(argparse.Namespace(media="meta", allow_pipe=True), client=c, ask=lambda _: "1") is None
    assert "ads_read" in capsys.readouterr().out
    with pytest.raises(connect.ConnectError, match="端末"):
        connect.cmd_check(argparse.Namespace(media="meta"), client=c, ask=lambda _: "1")
