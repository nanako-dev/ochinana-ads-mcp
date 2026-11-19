"""レポートの HTML（report_to_html）。束から図を描き、文と表は Markdown をそのまま変換する。外へは送らない。"""
from datetime import date

import pytest

from ochinana_ads import config, numcheck, report, report_html, tools

TODAY = date(2026, 11, 19)


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_OUT_DIR, str(tmp_path / "out"))


def bundle():
    rows = [dict(date=f"2026-11-{11 + i:02d}", key="1", name="A", impressions=100, clicks=10, cost=1000, conversions=1 if i % 2 else 0, value=0)
            for i in range(7)]
    prev = [dict(date=f"2026-11-{4 + i:02d}", key="1", name="A", impressions=90, clicks=9, cost=900, conversions=1, value=0)
            for i in range(7)]
    b, nums = report.build(rows, prev, start=date(2026, 11, 11), end=date(2026, 11, 17), cstart=date(2026, 11, 4),
                           cend=date(2026, 11, 10), compare_label="直前", today=TODAY, head={"対象アカウント": "テスト"})
    numcheck.save_bundle(b, nums)
    return b


MD = """# 週次の運用レポート（自分用） 2026-11-11〜2026-11-17

- 対象: テスト
- 束ID: {bid}

## 1. いちばん大事なこと
- 【事実】費用は 7,000 円。<script>alert(1)</script>
- 【推測】曜日の差かもしれない。

## 3. 数字
| 指標 | 今 |
|---|--:|
| 費用 | 7,000 円 |

## 4. 内訳
- 【事実】A だけ。

## 8. ネクストアクション
1. **確かめる: 成果の内訳を見る**
   - なぜ: 件数が少ない
   - 段: 確かめる／誰の案: AI の提案（未承認）／承認: 不要
2. **様子を見る: 待つ**
   - 段: 様子を見る／承認: 不要
"""


def test_ページの形と図と印():
    b = bundle()
    page = report_html.render(MD.format(bid=b["束ID"]), b)
    assert page.startswith("<!doctype html>") and '<html lang="ja">' in page and "viewport" in page
    assert page.count('<section class="sec">') == 4 and '<div class="kpi">' in page
    assert page.count("<svg") == 2  # 日別と内訳
    assert "【事実】" not in page and '<span class="k fact"><i></i>事実</span>' in page and 'class="k guess"' in page
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert '<td class="n">7,000 円</td>' in page
    assert '<ol class="acts">' in page and page.count('<div class="on">') == 2  # 確かめる・様子を見る の段が光る
    assert "url(#hatch)" in page  # 成果が未確定の日は斜線


def test_手元のmdの隣にhtmlを書き_数字の突き合わせも返す(tmp_path):
    b = bundle()
    p = tmp_path / "w.md"
    p.write_text(MD.format(bid=b["束ID"]), encoding="utf-8")
    out = tools.report_to_html(str(p))
    assert out["保存先"] == str(tmp_path / "w.html") and (tmp_path / "w.html").is_file()
    assert out["数字の突き合わせ"]["結果"] == "全部一致"
    p.write_text(MD.format(bid=b["束ID"]).replace("7,000 円。", "8,000 円。"), encoding="utf-8")
    assert tools.report_to_html(str(p))["数字の突き合わせ"]["件数"]["束に無い"] == 1


def test_断るもの(tmp_path):
    with pytest.raises(ValueError, match=".md"):
        tools.report_to_html(str(tmp_path / "x.txt"))
    with pytest.raises(ValueError, match="束ID"):
        tools.report_to_html(text="# 束の無い文")
    with pytest.raises(ValueError, match="見つからない"):
        tools.report_to_html(text="# x\n- 束ID: R20261117-0123abcd")
