"""Google 広告スクリプトの下書き（スクリプト/日別データを書き出す.js）の点検。Node は使わない。

読むだけであること（アカウントを変える呼び出しが無い）、DRY_RUN の既定が true、シートの URL が見本のままであること、
書く列が sheet.py の「日別データ」の見出しと同じであることを、文字で確かめる。
"""
import json
import re
from pathlib import Path

import pytest

from ochinana_ads import sheet

JS = Path(__file__).resolve().parents[1] / "スクリプト" / "日別データを書き出す.js"
MUTATING = ["pause(", "enable(", "remove(", "setBid", "setAmount", "applyLabel", "create", "newKeywordBuilder",
            "newAd", "mutate", "setBudget", "setCpc", "addLabel", "removeLabel", "bidding()", "build(", "insertSheet",
            "deleteSheet", "deleteRows", "deleteColumns", "insertColumn", "setFormula"]

pytestmark = pytest.mark.skipif(not JS.is_file(), reason="スクリプトの下書きが無い環境")


def _src() -> str:
    return JS.read_text(encoding="utf-8")


def test_アカウントを変える呼び出しが無い():
    s = _src()
    found = [w for w in MUTATING if w.lower() in s.lower()]
    assert found == []
    assert "AdsApp.search(" in s
    # AdsApp を使うのは、読む 2 つ（アカウントのタイムゾーンとレポート）だけ
    assert set(re.findall(r"AdsApp\.(\w+)", s)) == {"search", "currentAccount"}
    for f in ("campaign.id", "campaign.name", "segments.date", "metrics.impressions", "metrics.clicks",
              "metrics.cost_micros", "metrics.conversions", "metrics.conversions_value"):
        assert f in s


def test_DRY_RUN_の既定は_true_でシートの_URL_は見本():
    s = _src()
    assert re.search(r"DRY_RUN:\s*true\b", s) and not re.search(r"DRY_RUN:\s*false\b", s)
    url = re.search(r"SHEET_URL:\s*'([^']*)'", s).group(1)
    assert "ここに" in url and not re.search(r"/d/[A-Za-z0-9_-]{20,}", url)
    assert re.search(r"LOOKBACK_DAYS:\s*(\d+)", s) and 7 <= int(re.search(r"LOOKBACK_DAYS:\s*(\d+)", s).group(1)) <= 30


def test_書く列は日別データの_A_から_I_と同じ():
    s = _src()
    m = re.search(r"var HEADER = (\[[^\]]*\]);", s, re.S)
    assert json.loads(m.group(1).replace("'", '"')) == sheet.DATA_HEAD
    assert "'日別データ'" in s and sheet.DATA_TAB == "日別データ"
    # シートに触るのは、1 行目の読み取り・A〜I の読み取り・A〜I の中身の消去・A〜I への書き込みだけ（幅は WIDTH = 9 列）
    assert set(re.findall(r"\.(set\w+|clear\w*)\(", s)) == {"setValues", "clearContent"}
    assert all(", WIDTH)" in line for line in s.splitlines() if "getRange(" in line)
    assert "1000000" in s  # 費用は micros ÷ 1,000,000
