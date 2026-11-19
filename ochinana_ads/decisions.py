"""判断の記録。手元のファイル（log/decisions.jsonl）に 1 行ずつ足す。外へは送らない。"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import gaql
from .config import ROOT

ENV_LOG_DIR = "OCHINANA_ADS_LOG_DIR"
DECIDED_BY = ("AI の提案", "運用者の承認", "運用者の却下")
MAX_TEXT = 2000


def log_path() -> Path:
    folder = Path(os.environ.get(ENV_LOG_DIR) or ROOT / "log")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "decisions.jsonl"


def _text(name: str, v, required: bool = True) -> str:
    s = "" if v is None else str(v).strip()
    if required and not s:
        raise ValueError(f"{name}が空。何を・なぜを言葉で書く")
    if len(s) > MAX_TEXT:
        raise ValueError(f"{name}が長すぎる（{len(s)} 文字。{MAX_TEXT} 文字まで）")
    return s


def _ids(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in v.replace("、", ",").split(",")]
    return [str(x).strip() for x in v if str(x).strip()]


def log_decision(customer_id: str, decision: str, reason: str, decided_by: str,
                 check_ids=None, card_ids=None, target: str = "", now: Optional[datetime] = None) -> dict:
    cid = gaql.normalize_customer_id(customer_id)
    if decided_by not in DECIDED_BY:
        raise ValueError(f"誰の判断かは {' / '.join(DECIDED_BY)} のどれか（受け取った値: {decided_by!r}）")
    now = now or datetime.now().astimezone()
    record = {
        "id": f"D-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        "日時": now.isoformat(timespec="seconds"),
        "アカウント": cid,
        "対象": _text("対象", target, required=False),
        "判断": _text("判断", decision),
        "理由": _text("理由", reason),
        "根拠の点検": _ids(check_ids),
        "根拠の知見カード": _ids(card_ids),
        "誰の判断": decided_by,
    }
    path = log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"記録した内容": record, "保存先": str(path)}


def read_all() -> List[dict]:
    path = log_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"読めない行": line[:200]})
    return rows


def list_decisions(customer_id: Optional[str] = None, limit: int = 20) -> dict:
    cid = gaql.normalize_customer_id(customer_id) if customer_id else None
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20
    rows = [r for r in read_all() if cid is None or r.get("アカウント") == cid]
    by = {}
    for r in rows:
        by[r.get("誰の判断", "不明")] = by.get(r.get("誰の判断", "不明"), 0) + 1
    return {
        "対象アカウント": cid or "すべて",
        "期間": "記録の全期間",
        "単位": "判断 1 件ごと（新しい順）",
        "件数": {"全体": len(rows), "表示している件数": min(limit, len(rows)), "誰の判断か": by},
        "行": list(reversed(rows))[:limit],
        "保存先": str(log_path()),
    }
