"""決め事を 1 か所に置く。鍵そのものはここにも、このフォルダのどこにも置かない。"""
from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.17.0"
UPDATED = "2026-11-22"

# 読んでよい Google 広告のアカウント（許可リスト）。環境変数 ADS_ALLOWED_CUSTOMER_IDS にカンマ区切りで書く
# （例: 1234567890,2345678901）。空なら、どのアカウントも API を呼ぶ前に断る。増やすのは運用者本人。
ENV_ALLOWED_IDS = "ADS_ALLOWED_CUSTOMER_IDS"
ALLOWED_CUSTOMER_IDS = tuple(x.strip().replace("-", "") for x in os.environ.get(ENV_ALLOWED_IDS, "").split(",") if x.strip())

# 検証中で変えないキャンペーン（名前 → (最終日, 理由)）。レポートの「次にやること」で、変える案を「様子を見る」に回す。
# 環境変数 ADS_FROZEN_CAMPAIGNS に JSON で書く（例: {"キャンペーン名": ["2026-12-31", "LP の検証中"]}）。
ENV_FROZEN = "ADS_FROZEN_CAMPAIGNS"


def _frozen() -> dict:
    import json
    raw = os.environ.get(ENV_FROZEN, "").strip()
    if not raw:
        return {}
    try:
        return {str(k): (str(v[0]), str(v[1])) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, IndexError, AttributeError):
        raise SystemExit(f"{ENV_FROZEN} の JSON が読めない（例: {{\"名前\": [\"2026-12-31\", \"理由\"]}}）")


FROZEN_CAMPAIGNS = _frozen()

# 認証ファイルの場所を指す環境変数（google-ads ライブラリの標準の名前）。
# 値は「ファイルの場所」だけで、鍵の中身ではない。中身はライブラリが直接読む。
ENV_CREDENTIALS_PATH = "GOOGLE_ADS_CONFIGURATION_FILE_PATH"

# 全件の CSV を置く場所。変えたいときだけ環境変数で指す。
ENV_OUT_DIR = "OCHINANA_ADS_OUT_DIR"

ROOT = Path(__file__).resolve().parent.parent
PRINCIPLES_PATH = ROOT / "principles.md"

DEFAULT_TOP_N = 20
MAX_TOP_N = 100


def out_dir() -> Path:
    p = Path(os.environ.get(ENV_OUT_DIR) or ROOT / "out")
    p.mkdir(parents=True, exist_ok=True)
    return p
