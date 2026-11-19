"""判断の記録。手元のファイルにだけ書くことを確かめる。"""
import json
from datetime import datetime, timezone

import pytest

from ochinana_ads import decisions


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    monkeypatch.setenv(decisions.ENV_LOG_DIR, str(tmp_path))
    return tmp_path


def test_1行ずつ足す(_log):
    now = datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc)
    a = decisions.log_decision("111-222-3333", "「live」を除外語句にする案", "クリック 0 で表示 12 回", "AI の提案",
                               check_ids=["C06"], target="Search_A", now=now)
    decisions.log_decision("1112223333", "除外語句の案を承認", "ページの内容と合わない", "運用者の承認",
                           check_ids="C06, C10", card_ids=["K-001"])
    lines = (_log / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == a["記録した内容"]
    assert first["アカウント"] == "1112223333"
    assert first["日時"] == "2026-11-11T09:30:00+00:00"
    assert first["id"].startswith("D-20261111-093000-")
    assert first["根拠の点検"] == ["C06"] and first["誰の判断"] == "AI の提案"
    assert json.loads(lines[1])["根拠の点検"] == ["C06", "C10"]
    assert json.loads(lines[1])["根拠の知見カード"] == ["K-001"]
    assert "除外語句" in lines[0]  # 日本語をそのまま書く（\\u にしない）


def test_誰の判断かは3つだけ():
    for ok in ("AI の提案", "運用者の承認", "運用者の却下"):
        decisions.log_decision("1112223333", "x", "y", ok)
    for bad in ("承認", "AI", "", "運用者"):
        with pytest.raises(ValueError, match="誰の判断"):
            decisions.log_decision("1112223333", "x", "y", bad)


def test_許可していないアカウントと空の内容は断る(_log):
    with pytest.raises(ValueError, match="許可リスト"):
        decisions.log_decision("1234567890", "x", "y", "AI の提案")
    with pytest.raises(ValueError, match="判断"):
        decisions.log_decision("1112223333", "  ", "y", "AI の提案")
    with pytest.raises(ValueError, match="理由"):
        decisions.log_decision("1112223333", "x", "", "AI の提案")
    assert not (_log / "decisions.jsonl").exists()


def test_新しい順に読む():
    for i in range(5):
        decisions.log_decision("1112223333", f"判断{i}", "理由", "AI の提案")
    decisions.log_decision("4445556666", "別のアカウント", "理由", "運用者の却下")
    out = decisions.list_decisions("1112223333", limit=3)
    assert list(out)[:3] == ["対象アカウント", "期間", "単位"]
    assert [r["判断"] for r in out["行"]] == ["判断4", "判断3", "判断2"]
    assert out["件数"]["全体"] == 5
    everything = decisions.list_decisions(None)
    assert everything["件数"]["全体"] == 6
    assert everything["件数"]["誰の判断か"] == {"AI の提案": 5, "運用者の却下": 1}
    assert everything["行"][0]["判断"] == "別のアカウント"


def test_記録が無いときは空で返す():
    out = decisions.list_decisions("1112223333")
    assert out["行"] == [] and out["件数"]["全体"] == 0
