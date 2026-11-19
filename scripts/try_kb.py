"""動作確認（知識ベースの道具）。サーバーを stdio で起動し、運用の型・理解度テストの道具を MCP の呼び出しで返すか確かめる。

広告アカウントにはつながない（認証ファイルは要らない）。外へも何も送らない。
    .venv/bin/python scripts/try_kb.py            # 各返り値の先頭 10 行
    .venv/bin/python scripts/try_kb.py 40         # 先頭 40 行
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def head(result, lines: int) -> tuple[bool, str, dict]:
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    try:
        data = json.loads(text)
        text = json.dumps(data, ensure_ascii=False, indent=1)
    except ValueError:
        data = {}
    rows = text.splitlines()
    return not result.is_error, "\n".join(rows[:lines]) + ("\n…" if len(rows) > lines else ""), data


async def main() -> int:
    lines = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    env = {"PATH": os.environ.get("PATH", "")}
    if os.environ.get("ADS_KB_DIR"):
        env["ADS_KB_DIR"] = os.environ["ADS_KB_DIR"]
    params = StdioServerParameters(command=str(ROOT / ".venv" / "bin" / "python"), args=["-m", "ochinana_ads.server"],
                                   env=env, cwd=str(ROOT))
    calls = [
        ("① kb_overview", "kb_overview", {}),
        ("② kb_kata 規換-15", "kb_kata", {"query": "規換-15"}),
        ("③ kb_kata 状4 の規則", "kb_kata", {"state": "状4"}),
        ("④ kb_kata 閾08", "kb_kata", {"query": "閾08"}),
        ("⑤ kb_quiz 入札の分野から 3 問（正解なし）", "kb_quiz", {"field": "入札", "count": 3}),
        ("⑥ kb_check_claim", "kb_check_claim", {"claim": "予算を変えると学習がリセットされる"}),
    ]
    failed = 0
    async with Client(params) as client:
        names = [t.name for t in (await client.list_tools()).tools]
        print(f"道具 {len(names)} 個。kb_kata: {'kb_kata' in names}、kb_quiz: {'kb_quiz' in names}")
        for label, name, args in calls:
            ok, text, data = head(await client.call_tool(name, args), lines)
            failed += not ok
            print(f"\n===== {label} {'OK' if ok else '失敗'} =====\n{text}")
            if name == "kb_quiz" and ok:
                rows = data.get("行") or []
                if any("正解" in r for r in rows):
                    print("！ 問いの取り出しに正解が入っている")
                    failed += 1
                if rows:
                    ok2, text2, _ = head(await client.call_tool("kb_quiz", {"id": rows[0]["id"]}), lines)
                    failed += not ok2
                    print(f"\n===== ⑤' kb_quiz 答え合わせ（{rows[0]['id']}） {'OK' if ok2 else '失敗'} =====\n{text2}")
    print(f"\n失敗: {failed} 件")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
