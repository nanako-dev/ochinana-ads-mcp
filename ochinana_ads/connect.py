"""つなぐための小さな道具（本人が自分の端末で動かす。AI の道具ではない）。

    uv run ochinana-ads-connect token --client-secret ~/Downloads/client_secret_XXXX.json --out ~/.config/ochinana-ads/google-ads.yaml
    uv run ochinana-ads-connect check

- token: ブラウザで本人が許可し、受け取ったリフレッシュトークンを認証ファイルに書く。値は画面に出さない。
  ファイルは本人だけが読める権限（600）で作り、この道具のフォルダの中には置かせない。
- check: このログインで読めるアカウント（直接のものと、マネージャーの配下）を一覧にし、
  選んだ ID から許可リストの 1 行（ADS_ALLOWED_CUSTOMER_IDS=…）を作る。
  一覧には勤務先や取引先のアカウントが混じることがあるので、端末にだけ出す（AI に貼らない）。

広告アカウントは読むだけ。変える呼び出しは無い。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

from .config import ENV_CREDENTIALS_PATH

SCOPE = "https://www.googleapis.com/auth/adwords"
ROOT = Path(__file__).resolve().parent.parent
ENV_ALLOWED = "ADS_ALLOWED_CUSTOMER_IDS"


class ConnectError(RuntimeError):
    pass


# ---------------- token ----------------

def read_client_secret(path: Path) -> dict:
    """Google Cloud で落とした OAuth クライアントの JSON から、種類と ID・秘密を取る（値は返すだけで表示しない）。"""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConnectError(f"OAuth クライアントの JSON が見つからない: {path}")
    except ValueError:
        raise ConnectError("OAuth クライアントの JSON が読めない（Google Cloud の「認証情報」から落としたファイルを指す）")
    kind = "installed" if "installed" in data else ("web" if "web" in data else "")
    if not kind:
        raise ConnectError("OAuth クライアントの JSON の形が違う（installed か web の項目が無い）。"
                           "Google Cloud の「認証情報」で、種類「デスクトップ アプリ」の OAuth クライアントを作って落とす")
    body = data[kind]
    if not body.get("client_id") or not body.get("client_secret"):
        raise ConnectError("OAuth クライアントの JSON に client_id か client_secret が無い")
    return {"kind": kind, "client_id": body["client_id"], "client_secret": body["client_secret"], "raw": data}


def check_out_path(out: Path, force: bool = False) -> Path:
    out = Path(out).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        raise ConnectError(f"認証ファイルを、この道具のフォルダ（{ROOT}）の中には置かない。"
                           "誤って公開しないよう、外の場所（例: ~/.config/ochinana-ads/google-ads.yaml）を指す")
    if out.exists() and not force:
        raise ConnectError(f"{out} はもうある。上書きするなら --force を付ける")
    return out


def config_text(client_id: str, client_secret: str, refresh_token: str, login_customer_id: str = "") -> str:
    q = json.dumps  # YAML の二重引用符の文字列として書く
    lines = [
        "# ochinana-ads-connect token が作った Google 広告 API の認証ファイル。チャットに貼らない・リポジトリに入れない。",
        "# 開発者トークンは 2026-09-09 に廃止されたので書かない。",
        f"client_id: {q(client_id)}",
        f"client_secret: {q(client_secret)}",
        f"refresh_token: {q(refresh_token)}",
    ]
    if login_customer_id:
        lines.append(f"login_customer_id: {q(normalize_id(login_customer_id))}")
    else:
        lines.append("# マネージャー（MCC）経由で読むときだけ、マネージャーの 10 桁を書く")
        lines.append('# login_customer_id: "1234567890"')
    lines.append("use_proto_plus: true")
    return "\n".join(lines) + "\n"


def write_private(out: Path, text: str) -> None:
    """本人だけが読める権限（600）で書く。置き換えるときも、いったん別名で書いてから入れ替える。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)
    os.chmod(out, 0o600)


def normalize_id(cid: str) -> str:
    s = str(cid).replace("-", "").strip()
    if not (s.isdigit() and len(s) == 10):
        raise ConnectError(f"アカウントの ID は 10 桁の数字（ハイフンは有っても無くてもよい）: {cid!r}")
    return s


def get_refresh_token(secret: dict) -> str:
    """本人のブラウザで許可を受ける（手元の 127.0.0.1 で受け取る）。"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(secret["raw"], scopes=[SCOPE])
    creds = flow.run_local_server(
        host="127.0.0.1", port=0 if secret["kind"] == "installed" else 8080, open_browser=True,
        access_type="offline", prompt="consent",
        authorization_prompt_message="ブラウザで Google のログインと許可の画面を開きます。開かないときは、次の URL を開いてください:\n{url}\n",
        success_message="許可を受け取りました。このタブを閉じて、端末に戻ってください。",
    )
    if not getattr(creds, "refresh_token", None):
        raise ConnectError("リフレッシュトークンが返らなかった。Google アカウントの「サードパーティのアクセス」でこのアプリの許可を外してから、もう一度動かす")
    return creds.refresh_token


def cmd_token(args, fetch: Optional[Callable[[dict], str]] = None) -> Path:
    secret = read_client_secret(Path(args.client_secret))
    out = check_out_path(Path(args.out), args.force)
    if secret["kind"] == "web":
        print("注意: この OAuth クライアントは「ウェブ アプリケーション」です。承認済みのリダイレクト URI に "
              "http://127.0.0.1:8080/ を足してから続けてください（「デスクトップ アプリ」なら設定は要りません）。")
    token = (fetch or get_refresh_token)(secret)
    write_private(out, config_text(secret["client_id"], secret["client_secret"], token, args.login_customer_id or ""))
    print(f"\n認証ファイルを書きました（中身は表示しません）: {out}")
    print("次にすること:")
    print(f"  1. AI のアプリの設定で、環境変数 {ENV_CREDENTIALS_PATH} に上の場所を入れる")
    print(f"  2. 読むアカウントを選ぶ: {ENV_CREDENTIALS_PATH}={out} uv run ochinana-ads-connect check")
    print("OAuth の同意画面が「テスト中」のままだと、7 日でこの認証が切れます。続けて使うなら「本番」にしてください。")
    return out


# ---------------- check ----------------

ACCOUNT_Q = ("SELECT customer.id, customer.descriptive_name, customer.manager, customer.currency_code, "
             "customer.time_zone, customer.test_account FROM customer LIMIT 1")
CHILD_Q = ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, "
           "customer_client.level, customer_client.currency_code, customer_client.test_account, customer_client.status "
           "FROM customer_client WHERE customer_client.level >= 1")


def _rows(client, cid: str, query: str, login: str) -> list:
    client.login_customer_id = login
    svc = client.get_service("GoogleAdsService")
    return [r for batch in svc.search_stream(customer_id=cid, query=query) for r in batch.results]


def discover(client) -> List[dict]:
    """読めるアカウントを、直接のものとマネージャーの配下に分けて並べる（読むだけ）。"""
    from .client import accessible_customer_ids, explain_api_error

    keep = getattr(client, "login_customer_id", None)
    out: List[dict] = []
    seen = set()
    try:
        for cid in accessible_customer_ids(client):
            try:
                c = _rows(client, cid, ACCOUNT_Q, cid)[0].customer
            except Exception as ex:
                out.append({"ID": cid, "名前": "（読めない）", "種類": "?", "経路": "直接", "通貨": "", "理由": explain_api_error(ex)})
                seen.add(cid)
                continue
            kind = "マネージャー" if c.manager else ("テスト用" if c.test_account else "通常")
            out.append({"ID": cid, "名前": c.descriptive_name, "種類": kind, "経路": "直接", "通貨": c.currency_code})
            seen.add(cid)
            if not c.manager:
                continue
            try:
                kids = _rows(client, cid, CHILD_Q, cid)
            except Exception as ex:
                out[-1]["理由"] = "配下を読めなかった: " + explain_api_error(ex)
                continue
            for r in kids:
                k = r.customer_client
                kid = str(k.id)
                if kid in seen:
                    continue
                seen.add(kid)
                status = getattr(k.status, "name", str(k.status))
                kind = "マネージャー" if k.manager else ("テスト用" if k.test_account else "通常")
                out.append({"ID": kid, "名前": k.descriptive_name, "種類": kind, "経路": f"{cid} の配下",
                            "通貨": k.currency_code, "via": cid, **({"状態": status} if status != "ENABLED" else {})})
    finally:
        client.login_customer_id = keep
    return out


def allow_line(picked: List[dict]) -> dict:
    ids = [p["ID"] for p in picked]
    vias = sorted({p["via"] for p in picked if p.get("via")})
    notes = []
    if any(p["種類"] == "マネージャー" for p in picked):
        notes.append("マネージャーのアカウントには実績が無い。配下の通常のアカウントを選ぶ")
    if len(vias) == 1:
        notes.append(f"マネージャー経由のアカウントがあるので、認証ファイルに login_customer_id: \"{vias[0]}\" を書く")
    elif len(vias) > 1:
        notes.append("違うマネージャーの配下を混ぜて選んだ。1 つの認証ファイルの login_customer_id は 1 つだけなので、マネージャーごとに分ける")
    if any(p.get("via") for p in picked) and any(not p.get("via") for p in picked):
        notes.append("直接のアカウントとマネージャー経由のアカウントを混ぜた。login_customer_id を書くと、直接のアカウントが読めなくなることがある")
    return {"line": f"{ENV_ALLOWED}={','.join(ids)}", "notes": notes}


def cmd_check(args, client=None, ask: Callable[[str], str] = input) -> Optional[dict]:
    if not getattr(args, "allow_pipe", False) and not sys.stdout.isatty():
        raise ConnectError("check は、本人が自分の端末で動かすためのもの（一覧に勤務先や取引先のアカウントが混じることがあるので、"
                           "AI や別のプログラムの出力には出さない）。端末で直接動かす")
    if client is None:
        from .client import load_client

        client = load_client()
    rows = discover(client)
    print("このログインで読めるアカウント（この一覧は AI に貼らないでください）:\n")
    for i, r in enumerate(rows, 1):
        extra = f"  ※{r['状態']}" if r.get("状態") else ""
        extra += f"  ※{r['理由']}" if r.get("理由") else ""
        print(f"  {i:>2}. {r['ID'][:3]}-{r['ID'][3:6]}-{r['ID'][6:]}  {r['名前']}  [{r['種類']} / {r['経路']} / {r['通貨']}]{extra}")
    if not rows:
        print("  （無い）このログインの Google アカウントが、どの広告アカウントにも足されていない。")
        return None
    raw = ask("\nAI に読ませるアカウントの番号を、カンマ区切りで入れてください（何もせず Enter で終わり）: ").strip()
    if not raw:
        return None
    picked = []
    for x in raw.replace("、", ",").split(","):
        x = x.strip()
        if not x:
            continue
        if not x.isdigit() or not 1 <= int(x) <= len(rows):
            raise ConnectError(f"番号 {x!r} は一覧に無い")
        picked.append(rows[int(x) - 1])
    res = allow_line(picked)
    print("\nAI のアプリの設定（環境変数）に、次の 1 行を足してください:\n")
    print("  " + res["line"])
    for n in res["notes"]:
        print("  注意: " + n)
    print("\n足したら AI のアプリを起動し直し、AI に「check_connection で確かめて」と頼むと、つながったかが分かります。")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ochinana-ads-connect", description="Google 広告 API につなぐための小さな道具（本人が端末で動かす）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token", help="ブラウザで許可して、認証ファイルを作る")
    t.add_argument("--client-secret", required=True, help="Google Cloud で落とした OAuth クライアントの JSON")
    t.add_argument("--out", required=True, help="書く認証ファイルの場所（この道具のフォルダの外）")
    t.add_argument("--login-customer-id", default="", help="マネージャー経由で読むときの、マネージャーの 10 桁")
    t.add_argument("--force", action="store_true", help="同じ名前のファイルがあっても上書きする")
    c = sub.add_parser("check", help="読めるアカウントを一覧にし、許可リストの 1 行を作る")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "token":
            cmd_token(args)
        else:
            cmd_check(args)
    except ConnectError as ex:
        print(f"止めました: {ex}", file=sys.stderr)
        return 2
    except Exception as ex:  # 認証の値やトレースは出さない
        from .client import SetupError, explain_api_error, explain_auth_error

        if isinstance(ex, SetupError):
            msg = str(ex)
        elif getattr(ex, "failure", None) is not None:
            msg = explain_api_error(ex)
        elif type(ex).__module__.startswith("google.auth"):
            msg = explain_auth_error(ex)
        else:
            msg = f"止めました（{type(ex).__name__}）"
        print(msg, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
