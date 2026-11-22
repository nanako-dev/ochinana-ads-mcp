"""つなぐための道具（connect）と、接続の確かめ（check_connection）・エラーの直し方。本物の API もブラウザも使わない。"""
import argparse
import json
import os
import stat
from types import SimpleNamespace as NS

import pytest

from ochinana_ads import client as C
from ochinana_ads import config, connect, tools

SECRET = "s3cr3t-value-should-not-print"
TOKEN = "fake-refresh-value-should-not-print"


def _secret_file(tmp_path, kind="installed"):
    p = tmp_path / "client_secret.json"
    p.write_text(json.dumps({kind: {"client_id": "123.apps.googleusercontent.com", "client_secret": SECRET}}), encoding="utf-8")
    return p


# ---------------- token ----------------

def test_token_は本人だけが読める権限で書き_値を画面に出さない(tmp_path, capsys):
    out = tmp_path / "conf" / "google-ads.yaml"
    args = argparse.Namespace(client_secret=str(_secret_file(tmp_path)), out=str(out), force=False, login_customer_id="")
    connect.cmd_token(args, fetch=lambda s: TOKEN)
    text = out.read_text(encoding="utf-8")
    assert f'refresh_token: "{TOKEN}"' in text and f'client_secret: "{SECRET}"' in text
    assert "developer_token" not in text.replace("開発者トークンは", "")  # 廃止されたので書かない
    assert "use_proto_plus: true" in text
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    shown = capsys.readouterr().out
    assert TOKEN not in shown and SECRET not in shown
    assert "7 日" in shown


def test_token_の設定は_ライブラリがそのまま読める(tmp_path):
    from google.ads.googleads import config as gconf  # 設定の読み取りだけ（client を作ると Google へ問い合わせるので作らない）

    out = tmp_path / "google-ads.yaml"
    connect.write_private(out, connect.config_text("id.apps", SECRET, TOKEN, "123-456-7890"))
    d = gconf.load_from_yaml_file(str(out))
    assert d["login_customer_id"] == "1234567890" and d["refresh_token"] == TOKEN
    assert not d.get("developer_token")  # 開発者トークンなしで通る（2026-09-09 から任意）


def test_token_はフォルダの中と上書きを断る(tmp_path):
    with pytest.raises(connect.ConnectError, match="フォルダ"):
        connect.check_out_path(connect.ROOT / "google-ads.yaml")
    f = tmp_path / "a.yaml"
    f.write_text("x")
    with pytest.raises(connect.ConnectError, match="--force"):
        connect.check_out_path(f)
    assert connect.check_out_path(f, force=True) == f.resolve()


def test_token_は形の違うJSONを断る(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"type": "service_account"}))
    with pytest.raises(connect.ConnectError, match="デスクトップ"):
        connect.read_client_secret(p)
    assert connect.read_client_secret(_secret_file(tmp_path, "web"))["kind"] == "web"
    with pytest.raises(connect.ConnectError, match="10 桁"):
        connect.config_text("a", "b", "c", "12345")


# ---------------- check（本人の端末） ----------------

class FakeClient:
    """ListAccessibleCustomers と GAQL のにせ。login_customer_id の付け方も記録する。"""

    def __init__(self, direct, accounts, children):
        self.direct, self.accounts, self.children = direct, accounts, children
        self.login_customer_id = None
        self.calls = []

    def get_service(self, name):
        me = self
        if name == "CustomerService":
            return NS(list_accessible_customers=lambda: NS(resource_names=[f"customers/{d}" for d in me.direct]))

        def search_stream(customer_id, query):
            me.calls.append((customer_id, me.login_customer_id, query.split(" FROM ")[1].split()[0]))
            if "FROM customer_client" in query:
                return [NS(results=me.children.get(customer_id, []))]
            return [NS(results=[NS(customer=me.accounts[customer_id])])]

        return NS(search_stream=search_stream)


def _acct(name, manager=False, test=False, cur="JPY"):
    return NS(descriptive_name=name, manager=manager, test_account=test, currency_code=cur, time_zone="Asia/Tokyo")


def _child(cid, name, manager=False):
    return NS(customer_client=NS(id=cid, descriptive_name=name, manager=manager, level=1, currency_code="JPY",
                                 test_account=False, status=NS(name="ENABLED")))


def _fake():
    return FakeClient(
        direct=["1112223333", "9998887777"],
        accounts={"1112223333": _acct("自分の店"), "9998887777": _acct("MCC", manager=True)},
        children={"9998887777": [_child(4445556666, "子 A"), _child(1112223333, "自分の店")]},
    )


def test_check_は直接と配下を並べ_重ねない():
    fc = _fake()
    rows = connect.discover(fc)
    assert [(r["ID"], r["経路"]) for r in rows] == [
        ("1112223333", "直接"), ("9998887777", "直接"), ("4445556666", "9998887777 の配下")]
    assert ("9998887777", "9998887777", "customer_client") in fc.calls  # 配下はマネージャーを login にして読む
    assert fc.login_customer_id is None  # 終わったら元に戻す


def test_check_は選んだ番号から許可リストの1行を作る(capsys):
    args = argparse.Namespace(allow_pipe=True)
    res = connect.cmd_check(args, client=_fake(), ask=lambda _: "3")
    assert res["line"] == "ADS_ALLOWED_CUSTOMER_IDS=4445556666"
    assert any('login_customer_id: "9998887777"' in n for n in res["notes"])
    shown = capsys.readouterr().out
    assert "AI に貼らない" in shown and "ADS_ALLOWED_CUSTOMER_IDS=4445556666" in shown
    res = connect.cmd_check(args, client=_fake(), ask=lambda _: "1、2")
    assert res["line"] == "ADS_ALLOWED_CUSTOMER_IDS=1112223333,9998887777"
    assert any("マネージャーのアカウントには実績が無い" in n for n in res["notes"])


def test_check_は端末でないと一覧を出さない(capsys, monkeypatch):
    monkeypatch.setattr(connect.sys.stdout, "isatty", lambda: False, raising=False)
    with pytest.raises(connect.ConnectError, match="端末"):
        connect.cmd_check(argparse.Namespace(), client=_fake(), ask=lambda _: "1")
    assert "自分の店" not in capsys.readouterr().out


def test_check_の番号の誤りを断る():
    with pytest.raises(connect.ConnectError, match="一覧に無い"):
        connect.cmd_check(argparse.Namespace(allow_pipe=True), client=_fake(), ask=lambda _: "9")


# ---------------- エラーの直し方 ----------------

def _api_error(name, field="authorization_error"):
    from google.ads.googleads.v26.errors.types.errors import ErrorCode, GoogleAdsError, GoogleAdsFailure
    from google.ads.googleads.v26.errors.types import authorization_error as ae, quota_error as qe

    enum = ae.AuthorizationErrorEnum.AuthorizationError if field == "authorization_error" else qe.QuotaErrorEnum.QuotaError
    ex = Exception("x")
    ex.failure = GoogleAdsFailure(errors=[GoogleAdsError(error_code=ErrorCode(**{field: getattr(enum, name)}), message="m")])
    ex.request_id = "r"
    return ex


def test_よくあるエラーに直し方を付ける():
    assert "Explorer を申請" in C.explain_api_error(_api_error("CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"))
    assert "login_customer_id" in C.explain_api_error(_api_error("USER_PERMISSION_DENIED"))
    assert "2,880" in C.explain_api_error(_api_error("RESOURCE_EXHAUSTED", "quota_error"))
    assert "Basic 以上" in C.explain_api_error(_api_error("USER_PERMISSION_DENIED"), keyword_planner=True)
    assert "既知の問題" in C.explain_api_error(_api_error("ACTION_NOT_PERMITTED"))


def test_リフレッシュトークンの期限切れは7日の話を添える():
    class RefreshError(Exception):
        pass

    RefreshError.__module__ = "google.auth.exceptions"
    text = C.explain_auth_error(RefreshError("invalid_grant: Token has been expired or revoked."))
    assert "7 日" in text and "invalid_grant" not in text.split("。")[0][:5]


# ---------------- check_connection（AI の道具） ----------------

@pytest.fixture()
def creds(tmp_path, monkeypatch):
    f = tmp_path / "google-ads.yaml"
    f.write_text("use_proto_plus: true\n")
    os.chmod(f, 0o600)
    monkeypatch.setenv(config.ENV_CREDENTIALS_PATH, str(f))
    tools._accounts.clear()
    yield f
    tools._accounts.clear()


def _run(cid, query):
    return [NS(customer=NS(id=int(cid), descriptive_name=f"名前{cid[-2:]}", currency_code="JPY", time_zone="Asia/Tokyo", manager=False))]


def test_check_connection_は許可リストに無いアカウントを出さない(creds, monkeypatch):
    allowed = tools.ALLOWED_CUSTOMER_IDS
    hidden = "7777777777"
    out = tools.check_connection(run=_run, accessible=lambda: [allowed[0], hidden])
    text = json.dumps(out, ensure_ascii=False)
    assert hidden not in text
    assert "2 件" in text
    acc = out["段"][2]["アカウント"]
    assert [a["ID"] for a in acc] == list(allowed)
    assert acc[0]["直接の権限"] == "ある"
    if len(allowed) > 1:
        assert "login_customer_id" in acc[1]["直接の権限"]
    assert out["結論"].startswith("つながった")


def test_check_connection_は認証ファイルが無ければ止める(monkeypatch):
    monkeypatch.delenv(config.ENV_CREDENTIALS_PATH, raising=False)
    out = tools.check_connection(run=_run, accessible=lambda: [])
    assert out["段"][0]["結果"] == "直す" and "止めた" in out["結論"]


def test_check_connection_は権限の広いファイルを知らせる_中身は読まない(creds):
    os.chmod(creds, 0o644)
    creds.write_text(f"refresh_token: {TOKEN}\n")
    out = tools.check_connection(run=_run, accessible=lambda: list(tools.ALLOWED_CUSTOMER_IDS))
    text = json.dumps(out, ensure_ascii=False)
    assert "chmod 600" in text and TOKEN not in text


def test_check_connection_はAPIの失敗を言い換える(creds):
    def boom():
        raise _api_error("CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION")

    out = tools.check_connection(run=_run, accessible=boom)
    assert out["段"][1]["結果"] == "直す" and "Explorer" in out["段"][1]["理由"]
