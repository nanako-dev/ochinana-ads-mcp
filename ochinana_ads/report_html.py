"""書き上がったレポート（Markdown）を、手元で見る HTML のページにする。外へは何も送らない（公開もしない）。

- 数字は打ち直さない: 文と表は Markdown のまま変換し、図は数字の束（out/report_bundles/束ID.json）から描く
- 見た目は運用サイトの案 B「設計図」に合わせる（白地・淡いセージ・黒い線・等幅の小さなラベル・緑の信号色は図と強調だけ）。
  左の色の帯・文中の枠つきの札は使わない（運用者の指定）。事実・推測・提案は文頭の小さな点と文字で示す
- 読める Markdown は、このセットのレポートが使う形だけ（見出し・箇条・番号つき箇条・表・段落・太字・コード）
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import numcheck, templates

FONT_LINK = ("https://fonts.googleapis.com/css2?family=Chivo+Mono:wght@400;500&"
             "family=Noto+Sans+JP:wght@400;700;900&display=swap")

CSS = """
:root{--bg:#fff;--sage:#F5F8F4;--ink:#000;--sub:#4D4D4D;--muted:#676E64;--rule:#D9D9D9;--edge:#909A8B;
--signal:#19E76E;--signal-ink:#0B8F45;--signal-pale:#C9F7DC;
--sans:'Noto Sans JP','Hiragino Sans',system-ui,sans-serif;--mono:'Chivo Mono',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.75}
main{max-width:1040px;margin:0 auto;padding:0 16px 64px}
.tag{font-family:var(--mono);font-size:12px;color:var(--muted);margin:28px 0 8px}
h1{font-size:34px;line-height:1.2;font-weight:900;letter-spacing:-.01em;margin:0 0 12px}
.meta{font-family:var(--mono);font-size:12px;color:var(--sub);margin:0;padding:0;list-style:none;border-top:1px dashed var(--rule)}
.meta li{padding:6px 0;border-bottom:1px dashed var(--rule)}
.meta b{display:inline-block;min-width:6.5em;color:var(--muted);font-weight:500}
.kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid #000;margin:24px 0 0}
.kpi div{padding:14px 16px;border-right:1px dashed var(--edge);background:var(--sage);min-width:0}
.kpi div:last-child{border-right:0}
.kpi span{display:block;font-size:12px;color:var(--muted)}
.kpi b{display:block;font-family:var(--mono);font-size:26px;font-weight:500;line-height:1.35;white-space:nowrap}
.kpi small{font-family:var(--mono);font-size:12px;color:var(--sub)}
.fig-box{border:1px solid #000;padding:12px;background:#fff;overflow-x:auto;margin-top:16px;
background-image:radial-gradient(#E3E3E3 1px,transparent 1px);background-size:16px 16px}
.fig{display:block;width:100%;min-width:640px;height:auto}
.cap{font-size:12px;color:var(--muted);margin:8px 0 0;font-family:var(--mono)}
.sec{display:grid;grid-template-columns:56px minmax(0,1fr);gap:16px;padding:32px 0;border-bottom:1px dashed var(--rule)}
.sec>div{min-width:0}
.num{font-family:var(--mono);font-size:13px;border:1px solid var(--edge);height:26px;display:grid;place-items:center;margin-top:4px;background:var(--sage)}
h2{font-size:21px;font-weight:900;margin:0 0 12px}
p{margin:0 0 10px}
ul{margin:0 0 10px;padding:0;list-style:none}
ul>li{position:relative;padding-left:16px;margin:4px 0}
ul>li::before{content:"";position:absolute;left:3px;top:.72em;width:5px;height:5px;background:#000}
ul>li.lab{padding-left:0}
ul>li.lab::before{display:none}
li>ul{margin:4px 0 6px}
.k{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11.5px;color:var(--sub);margin-right:6px;white-space:nowrap}
.k i{width:9px;height:9px;border-radius:50%;background:#000;display:inline-block}
.k.guess i{background:#fff;border:1.5px solid #000}
.k.prop i{background:var(--signal);outline:1px solid #000}
.tbl{border:1px solid #000;overflow-x:auto;margin:6px 0 14px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:7px 12px;border-bottom:1px dashed var(--rule);text-align:left;vertical-align:top}
tbody tr:last-child td{border-bottom:0}
thead th{font-family:var(--mono);font-size:11.5px;font-weight:500;color:var(--muted);background:var(--sage);border-bottom:1px solid #000;white-space:nowrap}
td.n{text-align:right;font-family:var(--mono);font-size:13px;white-space:nowrap}
code{font-family:var(--mono);font-size:12px;background:var(--sage);border:1px solid var(--rule);padding:0 4px}
.stages{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));border:1px solid #000;margin:4px 0 16px}
.stages div{padding:8px 6px;border-right:1px dashed var(--edge);font-size:12px;text-align:center;line-height:1.4;color:var(--muted)}
.stages div:last-child{border-right:0}
.stages div.on{background:var(--signal-pale);color:#000;font-weight:700}
.stages div small{display:block;font-family:var(--mono);font-size:11px;font-weight:400}
.acts{list-style:none;padding:0;margin:0;border-top:1px solid #000}
.acts>li{display:grid;grid-template-columns:40px minmax(0,1fr);gap:12px;padding:14px 0;border-bottom:1px dashed var(--rule);margin:0}
.acts>li>span{font-family:var(--mono);font-size:20px;line-height:1.3}
.acts>li>div>b{display:block;font-size:16px;margin-bottom:4px}
.acts ul{list-style:none;padding:0;margin:0;font-size:13.5px;color:var(--sub)}
.acts ul li{margin:2px 0;padding-left:0}
.acts ul li::before{display:none}
.foot{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:24px}
@media (max-width:720px){h1{font-size:26px}.kpi{grid-template-columns:repeat(2,minmax(0,1fr))}
.kpi div:nth-child(2){border-right:0}.kpi div:nth-child(-n+2){border-bottom:1px dashed var(--edge)}
.sec{grid-template-columns:1fr;gap:6px}.num{width:44px}.stages{grid-template-columns:repeat(3,minmax(0,1fr))}
.stages div{border-bottom:1px dashed var(--edge)}}
"""

LABELS = [("【事実】", "fact", "事実"), ("【推測】", "guess", "推測"), ("【提案・AI（未承認）】", "prop", "提案・AI（未承認）")]


# ---------------- 小さな Markdown ----------------

def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    for mark, cls, text in LABELS:
        s = s.replace(html.escape(mark, quote=False), f'<span class="k {cls}"><i></i>{text}</span>')
    return s


def _is_num(cell: str) -> bool:
    c = cell.strip()
    return bool(re.fullmatch(r"[+\-−]?[\d,]+(?:\.\d+)?\s?(?:円|%|pt|件|回)?|—", c))


def _table(lines: List[str]) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    head, body = rows[0], [r for r in rows[2:]]
    out = ['<div class="tbl"><table><thead><tr>' + "".join(f"<th>{_inline(h)}</th>" for h in head) + "</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join(f'<td class="n">{_inline(c)}</td>' if i and _is_num(c) else f"<td>{_inline(c)}</td>"
                                    for i, c in enumerate(r)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _li(text: str) -> str:
    lab = any(text.startswith(m) for m, _, _ in LABELS)
    return f'<li class="lab">{_inline(text)}</li>' if lab else f"<li>{_inline(text)}</li>"


def _list(lines: List[str]) -> str:
    """「- 」と、2 字以上の字下げの「- 」（1 段の入れ子）だけ。"""
    out, open_sub = ["<ul>"], False
    for ln in lines:
        sub = re.match(r"^\s{2,}- ", ln)
        text = re.sub(r"^\s*- ", "", ln)
        if sub:
            if not open_sub:
                out[-1] = out[-1].removesuffix("</li>") + "<ul>"
                open_sub = True
            out.append(_li(text))
        else:
            if open_sub:
                out.append("</ul></li>")
                open_sub = False
            out.append(_li(text))
    if open_sub:
        out.append("</ul></li>")
    out.append("</ul>")
    return "".join(out)


def _actions(lines: List[str]) -> str:
    """「1. **見出し**」と、その下の字下げの「- 」を、1 件ずつの行にする。"""
    items, cur = [], None
    for ln in lines:
        m = re.match(r"^(\d+)\.\s+(.*)", ln)
        if m:
            cur = {"n": m.group(1), "title": re.sub(r"^\*\*(.*)\*\*$", r"\1", m.group(2).strip()), "sub": []}
            items.append(cur)
        elif cur is not None and ln.strip().startswith("- "):
            cur["sub"].append(ln.strip()[2:])
    out = ['<ol class="acts">']
    for it in items:
        sub = "".join(f"<li>{_inline(s)}</li>" for s in it["sub"])
        out.append(f'<li><span>{html.escape(it["n"])}</span><div><b>{_inline(it["title"])}</b><ul>{sub}</ul></div></li>')
    out.append("</ol>")
    return "".join(out)


def _blocks(lines: List[str]) -> str:
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.append(_table(lines[i:j]))
            i = j
        elif re.match(r"^\s*- ", ln):
            j = i
            while j < len(lines) and re.match(r"^\s*- ", lines[j]):
                j += 1
            out.append(_list(lines[i:j]))
            i = j
        elif re.match(r"^\d+\.\s", ln):
            j = i
            while j < len(lines) and (re.match(r"^\d+\.\s", lines[j]) or re.match(r"^\s{2,}- ", lines[j])):
                j += 1
            out.append(_actions(lines[i:j]))
            i = j
        else:
            out.append(f"<p>{_inline(ln.strip())}</p>")
            i += 1
    return "".join(out)


def parse(md: str) -> dict:
    """見出し 1、その下の箇条（メタ）、## の節に分ける。"""
    title, meta, secs, cur = "", [], [], None
    for ln in md.splitlines():
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
        elif ln.startswith("## "):
            m = re.match(r"##\s+(\d+)\.\s*(.*)", ln)
            cur = {"no": m.group(1) if m else "", "name": (m.group(2) if m else ln[3:]).strip(), "lines": []}
            secs.append(cur)
        elif cur is None:
            if ln.startswith("- "):
                meta.append(ln[2:])
        else:
            cur["lines"].append(ln)
    return {"title": title, "meta": meta, "sections": secs}


# ---------------- 図（束から描く） ----------------

def _fmt_yen(v) -> str:
    return "—" if v is None else f"{v:,.0f} 円"


def kpi(bundle: dict) -> str:
    rows = {r["指標"]: r for r in bundle.get("合計", [])}
    lag = bundle.get("成果の遅れ", {}).get("未確定の日数", 0)
    cells = []
    for name, label in (("費用", "費用"), ("コンバージョン", "コンバージョン" + ("（暫定）" if lag else "")),
                        ("コンバージョン単価", "コンバージョン単価"), ("クリック数", "クリック数")):
        r = rows.get(name)
        if not r:
            continue
        u, d = r["単位"], 1 if name == "コンバージョン" else 0
        val = "—" if r["今"] is None else (f"{r['今']:,.{d}f}" + ("" if u in ("%",) else f" {u}"))
        prev = "—" if r["前"] is None else f"{r['前']:,.{d}f} {u}"
        diff = "" if r["差"] is None else f"（{r['差']:+,.{d}f}）"
        cells.append(f"<div><span>{html.escape(label)}</span><b>{html.escape(val)}</b><small>前 {html.escape(prev)}{html.escape(diff)}</small></div>")
    return f'<div class="kpi">{"".join(cells)}</div>' if cells else ""


def daily_svg(bundle: dict) -> str:
    days = bundle.get("日別", [])
    if not days:
        return ""
    W, H, L, R, T, B = 960, 250, 64, 56, 24, 46
    n = len(days)
    cw = (W - L - R) / n
    cmax = max([d["費用"] for d in days] + [1])
    vmax = max([d["コンバージョン"] for d in days] + [1])
    step = _nice(cmax)
    top = step * max(1, -(-cmax // step))
    vtop = max(1, -(-vmax // 1))
    y = lambda v: T + (H - T - B) * (1 - v / top)
    yv = lambda v: T + (H - T - B) * (1 - v / vtop)
    g = [f'<svg class="fig" viewBox="0 0 {W} {H}" role="img" aria-label="日別の費用とコンバージョン" xmlns="http://www.w3.org/2000/svg">',
         '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<rect width="6" height="6" fill="#fff"/><line x1="0" y1="0" x2="0" y2="6" stroke="#000" stroke-width="2"/></pattern></defs>']
    k = 0
    while k * step <= top:
        yy = y(k * step)
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#D9D9D9" stroke-dasharray="3 3"/>')
        g.append(f'<text x="{L - 8}" y="{yy + 4:.1f}" text-anchor="end" font-family="Chivo Mono,monospace" font-size="11" fill="#676E64">{k * step:,.0f}</text>')
        k += 1
    for i, d in enumerate(days):
        x = L + i * cw
        bw = cw * 0.56
        bx = x + (cw - bw) / 2
        yy = y(d["費用"])
        lag = bool(d.get("成果"))
        g.append(f'<rect x="{bx:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="{H - B - yy:.1f}" fill="#000"/>')
        g.append(f'<text x="{x + cw / 2:.1f}" y="{H - B + 16}" text-anchor="middle" font-family="Chivo Mono,monospace" font-size="11">{d["日付"][5:].replace("-", "/")}</text>')
        g.append(f'<text x="{x + cw / 2:.1f}" y="{H - B + 31}" text-anchor="middle" font-size="11" fill="#676E64">{d["曜日"]}</text>')
        c = d["コンバージョン"]
        if c:
            cy = yv(c)
            fill = "url(#hatch)" if lag else "#19E76E"
            g.append(f'<rect x="{x + cw / 2 - 7:.1f}" y="{cy - 7:.1f}" width="14" height="14" fill="{fill}" stroke="#000"/>')
            g.append(f'<text x="{x + cw / 2 + 11:.1f}" y="{cy + 4:.1f}" font-family="Chivo Mono,monospace" font-size="11">{c:g} 件</text>')
    for v in range(0, int(vtop) + 1):
        g.append(f'<text x="{W - R + 8}" y="{yv(v) + 4:.1f}" font-family="Chivo Mono,monospace" font-size="11" fill="#0B8F45">{v}</text>')
    g.append(f'<text x="{L - 8}" y="14" text-anchor="end" font-size="11" fill="#676E64">円</text>')
    g.append(f'<text x="{W - R + 8}" y="14" font-size="11" fill="#0B8F45">件</text>')
    g.append("</svg>")
    return "".join(g)


def _nice(v: float) -> float:
    raw = v / 4
    p = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * p:
            return m * p
    return 10 * p


def breakdown_svg(bundle: dict) -> str:
    rows = [r for r in bundle.get("内訳", []) if r.get("費用（今）")]
    if not rows:
        return ""
    W, rh, L = 960, 34, 360
    H = rh * len(rows) + 12
    total = sum(r["費用（今）"] for r in rows) or 1
    g = [f'<svg class="fig" viewBox="0 0 {W} {H}" role="img" aria-label="キャンペーンごとの費用" xmlns="http://www.w3.org/2000/svg">']
    for i, r in enumerate(rows):
        yy = 6 + i * rh
        w = (W - L - 170) * r["費用（今）"] / total
        name = r["対象"] if len(r["対象"]) <= 34 else r["対象"][:33] + "…"
        g.append(f'<text x="0" y="{yy + 20}" font-size="12.5">{html.escape(name)}</text>')
        g.append(f'<rect x="{L}" y="{yy + 6}" width="{max(w, 1):.1f}" height="18" fill="#19E76E" stroke="#000"/>')
        share = r.get("費用の構成比（%）")
        g.append(f'<text x="{L + w + 8:.1f}" y="{yy + 20}" font-family="Chivo Mono,monospace" font-size="12">'
                 f'{r["費用（今）"]:,} 円{f"（{share:g}%）" if share is not None else ""}</text>')
    g.append("</svg>")
    return "".join(g)


def stages_strip(lines: List[str]) -> str:
    used = set(re.findall(r"段: ([^／\s（]+)", "\n".join(lines)))
    cells = []
    for i, s in enumerate(templates.STAGES, 1):
        cells.append(f'<div class="{"on" if s in used else ""}"><small>{i:02d}</small>{html.escape(s)}</div>')
    return f'<div class="stages" aria-label="打ち手の段（色は今週の案がある段）">{"".join(cells)}</div>'


# ---------------- まとめ ----------------

def render(md: str, bundle: Optional[dict] = None) -> str:
    doc = parse(md)
    b = bundle or {}
    title = doc["title"] or "レポート"
    meta_html = []
    for m in doc["meta"]:
        k, _, v = m.partition(": ")
        meta_html.append(f"<li><b>{html.escape(k)}</b>{_inline(v)}</li>" if v else f"<li>{_inline(m)}</li>")
    head_tag, _, rest = title.partition(" ")
    body = [f'<p class="tag">{html.escape(head_tag)}</p><h1>{html.escape(rest or title)}</h1>',
            f'<ul class="meta">{"".join(meta_html)}</ul>', kpi(b)]
    fig = daily_svg(b)
    if fig:
        lagged = b.get("成果の遅れ", {}).get("未確定の日数", 0)
        body.append(f'<div class="fig-box">{fig}</div><p class="cap">日別。黒い棒が費用（左の目盛り・円）、四角がコンバージョン（右の目盛り・件）。'
                    f'{"斜線の四角は成果が未確定の日（後から足される）。" if lagged else ""}</p>')
    for s in doc["sections"]:
        inner = []
        if "ネクストアクション" in s["name"]:
            inner.append(stages_strip(s["lines"]))
        inner.append(_blocks(s["lines"]))
        if s["name"] == "内訳":
            svg = breakdown_svg(b)
            if svg:
                inner.insert(0, f'<div class="fig-box" style="margin:0 0 12px">{svg}</div>')
        body.append(f'<section class="sec"><div class="num">{html.escape(s["no"] or "—")}</div>'
                    f'<div><h2>{html.escape(s["name"])}</h2>{"".join(inner)}</div></section>')
    body.append(f'<p class="foot">数字は束 {html.escape(b.get("束ID", "—"))} から。図は束から描き、文と表は Markdown をそのまま変換した。</p>')
    return ("<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(title)}</title>"
            "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
            f"<link rel=\"stylesheet\" href=\"{FONT_LINK}\"><style>{CSS}</style></head>"
            f"<body><main>{''.join(body)}</main></body></html>")


def find_bundle_id(md: str) -> Optional[str]:
    m = re.search(r"R\d{8}-[0-9a-f]{8}", md)
    return m.group(0) if m else None


def load_bundle(bundle_id: str) -> dict:
    p = numcheck.bundle_path(bundle_id)
    if not p.exists():
        raise ValueError(f"束 {bundle_id} が見つからない（{p}）。図を描けないので、report_numbers か report_weekly_self を呼び直す")
    return json.loads(p.read_text(encoding="utf-8"))["束"]
