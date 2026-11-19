"""動画ファイルの仕様の点検。外へは何も送らない（ファイルは手元で読むだけ）。

- 仕様の数字は知識ベース 39（動画の仕様 点検用の表）の行を根拠に、下の表に置く。呼ぶたびにその行をいまの知識ベースから
  引き直し、数字がまだ書いてあるかを確かめて返す（kb.cite）。表に無い・未確認の決まりは「未確認」と返す。
- 動画の長さ・縦横・解像度・形式・音声の有無は、ffprobe → ffmpeg → MP4/MOV の箱を自分で読む、の順で測る。
  どれで測ったかを返す。ネットには出ない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import kb

KB = "39"
ENV_FFPROBE = "OCHINANA_FFPROBE_BIN"
ENV_FFMPEG = "OCHINANA_FFMPEG_BIN"
EXTRA_BIN_DIRS = (Path.home() / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
TIMEOUT_SEC = 30
EPS = 0.001  # 書き出しの端数（6.0000001 秒など）だけを許す

# ================= 向き =================

RATIOS: List[Tuple[str, float, str]] = [
    ("16:9", 16 / 9, "横"), ("9:16", 9 / 16, "縦"), ("1:1", 1.0, "正方形"),
    ("4:5", 4 / 5, "縦"), ("4:3", 4 / 3, "横"), ("2:3", 2 / 3, "縦"),
]
RECOMMENDED_RATIOS = ("16:9", "9:16", "1:1")
RATIO_TOLERANCE = 0.01  # 画像（15 の 1-68）と同じ ±1% で見る。動画の許容幅は知識ベースに無い

ACCEPTED_TYPES = {  # V-01。拡張子・コンテナの名前 → 表の名前
    "mpg": "MPG", "mpeg": "MPG", "m2v": "MPG", "mp4": "MP4", "m4v": "MP4", "mov": "MOV", "qt": "MOV",
    "wmv": "WMV", "avi": "AVI", "flv": "FLV", "3gp": "3GPP", "3g2": "3GPP", "webm": "WebM", "mkv": None, "gif": None,
}
AUDIO_ONLY = {"mp3", "wav", "pcm", "aac", "m4a", "flac", "ogg"}
MAX_BYTES = 256 * 1024 ** 3  # V-02

RES_TIERS = {  # V-05〜V-07: (推奨 1080p, HD の最小 720p, SD の最小)
    "横": ((1920, 1080), (1280, 720), (640, 480)),
    "縦": ((1080, 1920), (720, 1280), (480, 640)),
    "正方形": ((1080, 1080), (480, 480), (480, 480)),
}

# ================= フォーマットの枠（39 §2・§3） =================

S = dict
SLOTS: List[dict] = [
    S(fmt="スキップ可能なインストリーム", min=5, max=None, rec_max=180, ratios=None, rec_ratios=("16:9",),
      note="長さの上限なし。3 分未満を推奨。5 秒以上は動画再生回数キャンペーンの決まり。縦の動画も出ることがある",
      refs=[(KB, "V-10", ("3 分未満", "5 秒"))]),
    S(fmt="スキップ不可（15 秒枠・スキップ不可でのリーチ）", min=7, max=15, ratios=None, rec_ratios=("16:9",),
      refs=[(KB, "V-11", ("7〜15",)), (KB, "V-21", ("横 15 秒",))]),
    S(fmt="スキップ不可（30 秒枠・テレビ画面）", min=16, max=30, ratios=("16:9", "4:3"), must_horizontal=True,
      refs=[(KB, "V-12", ("16〜30", "横長"))]),
    S(fmt="スキップ不可（フリークエンシー目標設定）", min=7, max=20, alt_max=15, ratios=("16:9", "4:3"), must_horizontal=True,
      conflict="長さの上限は英語版ヘルプが 20 秒、日本語版が 15 秒と書く（39 の V-13 △）。両方で判定した。2026-11-18 の管理画面での確認では、18 秒のまま保存できた（英語版の 20 秒を支持。審査の結果は未確認）",
      refs=[(KB, "V-13", ("7〜20", "7〜15", "保存でき"))]),
    S(fmt="バンパー", min=5, max=6, ratios=None, rec_ratios=("16:9",),
      note="縦（9:16）の 6 秒もスキップ不可でのリーチの作成画面で受け付けられた（実際の配信面は未確認）。推奨の組み合わせは横 6 秒。"
           "YouTube は手元で測った長さより 0.1 秒ほど長く表示することがある（6.1 秒でもバンパーとして扱われた。39 の V-28）",
      refs=[(KB, "V-15", ("6 秒以下", "5 秒以上", "縦")), (KB, "V-28", ("0:06.1",))]),
    S(fmt="インフィード", min=5, max=None, rec=15, ratios=None,
      note="長さの上限なし。推奨 15 秒。5 秒以上は動画再生回数キャンペーンの決まり",
      refs=[(KB, "V-16", ("5 秒以上", "15 秒"))]),
    S(fmt="ショート", min=5, max=None, rec_max=60, ratios=None, rec_ratios=("9:16",),
      note="60 秒未満を推奨（フィードで再生されるのは最初の 60 秒）。横の動画は、広告グループに縦が無いときだけ出る。"
           "5 秒の下限はヘルプに書かれておらず、効率的なリーチの作成画面が 5 秒未満を断った管理画面での確認の結果（39 の V-27）",
      refs=[(KB, "V-17", ("60 秒未満",)), (KB, "V-18", ("縦の動画が無いとき",)), (KB, "V-27", ("5 秒以上",))]),
    S(fmt="デマンド ジェネレーション 動画", min=5, max=None, rec_min=15, ratios=("16:9", "1:1", "4:5", "9:16"),
      instream_min=10, note="1 広告 1〜5 本",
      refs=[(KB, "V-24", ("5 秒以上", "10 秒未満", "15 秒超", "4:5"))]),
    S(fmt="P-MAX 動画", min=10, max=None, ratios=("16:9", "1:1", "9:16"),
      note="横・正方形・縦を 1 本ずつ入れると自動生成の動画を防げる。0〜15 本",
      refs=[(KB, "V-25", ("10 秒以上", "16:9"))]),
    S(fmt="マストヘッド", min=None, max=None, rec_min=10, ratios=("16:9",),
      note="予約だけのフォーマット。長さは任意で 10 秒超を推奨", refs=[(KB, "V-19", ("10 秒超", "16:9"))]),
]

# 推奨の組み合わせ（39 §3）。この動画がどの「推奨の 1 本」に当たるか
COMBOS: List[dict] = [
    S(campaign="動画リーチ（効率的なリーチ）", piece="横 15 秒", orient="横", lo=14.5, hi=15.5, ref=(KB, "V-20", ("横 15 秒",))),
    S(campaign="動画リーチ（効率的なリーチ）", piece="横 6 秒", orient="横", lo=5, hi=6, ref=(KB, "V-20", ("横 6 秒",))),
    S(campaign="動画リーチ（効率的なリーチ）", piece="縦 6〜60 秒", orient="縦", lo=6, hi=60, ref=(KB, "V-20", ("縦 6〜60 秒",))),
    S(campaign="動画リーチ（効率的なリーチ）", piece="正方形 6〜60 秒", orient="正方形", lo=6, hi=60, ref=(KB, "V-20", ("正方形は 6〜60 秒",))),
    S(campaign="動画リーチ（スキップ不可でのリーチ）", piece="横 15 秒（7〜15 秒）", orient="横", lo=7, hi=15, ref=(KB, "V-21", ("横 15 秒",))),
    S(campaign="動画リーチ（スキップ不可でのリーチ）", piece="横 30 秒（16〜30 秒）", orient="横", lo=16, hi=30, ref=(KB, "V-21", ("横 30 秒",))),
    S(campaign="動画リーチ（スキップ不可でのリーチ）", piece="横 6 秒", orient="横", lo=5, hi=6, ref=(KB, "V-21", ("横 6 秒",))),
    S(campaign="動画リーチ（フリークエンシー目標設定）", piece="横 15 秒", orient="横", lo=14.5, hi=15.5, ref=(KB, "V-22", ("横 15 秒",))),
    S(campaign="動画リーチ（フリークエンシー目標設定）", piece="横 6 秒", orient="横", lo=5, hi=6, ref=(KB, "V-22", ("横 6 秒",))),
    S(campaign="動画再生回数（VVC）", piece="横 60 秒〜3 分", orient="横", lo=60, hi=180, ref=(KB, "V-23", ("60 秒〜3 分",))),
    S(campaign="動画再生回数（VVC）", piece="横 15 秒", orient="横", lo=14.5, hi=15.5, ref=(KB, "V-23", ("横 15 秒",))),
    S(campaign="動画再生回数（VVC）", piece="縦 10〜60 秒", orient="縦", lo=10, hi=60, ref=(KB, "V-23", ("縦 10〜60 秒",))),
]
COMBO_NOTE = "「15 秒」「6 秒」の推奨は幅が書かれていないので、15 秒は 14.5〜15.5 秒、6 秒はバンパーと同じ 5〜6 秒で見た"

FORMAT_ALIASES = {
    "スキップ可能": "スキップ可能なインストリーム", "インストリーム": "スキップ可能なインストリーム", "skippable": "スキップ可能なインストリーム",
    "instream": "スキップ可能なインストリーム", "スキップ不可": "スキップ不可", "nonskippable": "スキップ不可",
    "バンパー": "バンパー", "bumper": "バンパー", "インフィード": "インフィード", "infeed": "インフィード",
    "ショート": "ショート", "shorts": "ショート", "short": "ショート",
    "デマンドジェネレーション": "デマンド ジェネレーション 動画", "dg": "デマンド ジェネレーション 動画", "demandgen": "デマンド ジェネレーション 動画",
    "pmax": "P-MAX 動画", "p-max": "P-MAX 動画", "performancemax": "P-MAX 動画", "マストヘッド": "マストヘッド", "masthead": "マストヘッド",
}


# ================= 測る =================

def _find_bin(name: str, env: str) -> Optional[str]:
    v = os.environ.get(env)
    if v:
        return v if Path(v).exists() else None
    hit = shutil.which(name)
    if hit:
        return hit
    for d in EXTRA_BIN_DIRS:
        p = d / name
        if p.exists():
            return str(p)
    return None


def _ratio_name(w: int, h: int) -> Tuple[str, str]:
    r = w / h
    for name, value, orient in RATIOS:
        if abs(r / value - 1) <= RATIO_TOLERANCE:
            return name, orient
    return "その他", ("横" if r > 1 else "縦" if r < 1 else "正方形")


def _probe_ffprobe(binary: str, p: Path) -> dict:
    cmd = [binary, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    if out.returncode != 0:
        raise ValueError(f"ffprobe が読めなかった: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    rot = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(round(float(sd["rotation"])))
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    fps = None
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = round(float(n) / float(d), 3) if float(d) else None
    dur = (data.get("format") or {}).get("duration") or (v or {}).get("duration")
    return {"長さ（秒）": float(dur) if dur else None, "幅": v and v.get("width"), "高さ": v and v.get("height"),
            "回転": rot, "映像の形式": v and v.get("codec_name"), "フレームレート": fps,
            "音声": bool(a), "音声の形式": a and a.get("codec_name"),
            "コンテナ": (data.get("format") or {}).get("format_name"), "測り方": "ffprobe で測った"}


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID = re.compile(r"Stream #\S+.*?Video:\s*([\w\-]+).*?,\s*(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"([\d.]+)\s*fps")
_ROT = re.compile(r"rotation of\s*(-?[\d.]+)|rotate\s*:\s*(-?\d+)")
_INPUT = re.compile(r"Input #0,\s*([^,]+(?:,[^,\s]+)*),\s*from")


def _probe_ffmpeg(binary: str, p: Path) -> dict:
    out = subprocess.run([binary, "-hide_banner", "-i", str(p)], capture_output=True, text=True, timeout=TIMEOUT_SEC)
    err = out.stderr
    if "Invalid data" in err or "No such file" in err:
        raise ValueError("ffmpeg が動画として読めなかった")
    m = _DUR.search(err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    v = None
    for ln in err.splitlines():
        if "Video:" in ln and "attached pic" not in ln:
            v = ln
            break
    w = h = codec = fps = None
    if v:
        mv = _VID.search(v)
        if mv:
            codec, w, h = mv.group(1), int(mv.group(2)), int(mv.group(3))
        mf = _FPS.search(v)
        fps = float(mf.group(1)) if mf else None
    a = next((ln for ln in err.splitlines() if "Audio:" in ln), None)
    mr = _ROT.search(err)
    rot = int(round(float(mr.group(1) or mr.group(2)))) if mr else 0
    mi = _INPUT.search(err)
    return {"長さ（秒）": dur, "幅": w, "高さ": h, "回転": rot, "映像の形式": codec, "フレームレート": fps,
            "音声": bool(a), "音声の形式": (re.search(r"Audio:\s*([\w\-]+)", a).group(1) if a else None),
            "コンテナ": mi.group(1) if mi else None, "測り方": "ffmpeg の表示を読んで測った（ffprobe が無いため）"}


def _boxes(buf: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1 and i + 16 <= end:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            return
        yield typ.decode("latin-1"), i + hdr, i + size
        i += size


def _probe_mp4(p: Path) -> dict:
    """MP4・MOV の箱（moov）を読む。外部の道具が無いときの代わり。"""
    data = p.read_bytes() if p.stat().st_size <= 512 * 1024 ** 2 else None
    if data is None:
        raise ValueError("ffprobe・ffmpeg が無く、ファイルが大きすぎて自分では読めない（512 MB 超）。数字で渡す")
    moov = next(((s, e) for t, s, e in _boxes(data, 0, len(data)) if t == "moov"), None)
    if not moov:
        raise ValueError("MP4・MOV の形として読めなかった（moov が無い）。ffmpeg を入れるか、数字で渡す")
    dur = None
    tracks = []
    for t, s, e in _boxes(data, *moov):
        if t == "mvhd":
            ver = data[s]
            if ver == 1:
                ts, d = struct.unpack(">IQ", data[s + 20:s + 32])
            else:
                ts, d = struct.unpack(">II", data[s + 12:s + 20])
            dur = d / ts if ts else None
        if t == "trak":
            tr: Dict[str, Any] = {}
            for t2, s2, e2 in _boxes(data, s, e):
                if t2 == "tkhd":
                    w, h = struct.unpack(">II", data[e2 - 8:e2])
                    tr["w"], tr["h"] = w >> 16, h >> 16
                    a, b = struct.unpack(">ii", data[e2 - 44:e2 - 36])
                    tr["rot"] = 90 if (a == 0 and b > 0) else -90 if (a == 0 and b < 0) else 180 if a < 0 else 0
                if t2 == "mdia":
                    for t3, s3, e3 in _boxes(data, s2, e2):
                        if t3 == "hdlr":
                            tr["kind"] = data[s3 + 8:s3 + 12].decode("latin-1")
                        if t3 == "minf":
                            for t4, s4, e4 in _boxes(data, s3, e3):
                                if t4 == "stbl":
                                    for t5, s5, e5 in _boxes(data, s4, e4):
                                        if t5 == "stsd" and e5 - s5 >= 16:
                                            tr["codec"] = data[s5 + 12:s5 + 16].decode("latin-1")
            tracks.append(tr)
    v = next((t for t in tracks if t.get("kind") == "vide"), None)
    a = next((t for t in tracks if t.get("kind") == "soun"), None)
    return {"長さ（秒）": dur, "幅": v and v.get("w"), "高さ": v and v.get("h"), "回転": (v or {}).get("rot", 0),
            "映像の形式": v and v.get("codec"), "フレームレート": None, "音声": bool(a), "音声の形式": a and a.get("codec"),
            "コンテナ": "mp4/mov", "測り方": "MP4・MOV の箱を自分で読んで測った（ffprobe・ffmpeg が無いため。フレームレートは見ていない）"}


def measure(path: str) -> dict:
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        raise ValueError(f"動画のファイルが見つからない: {p}")
    ext = p.suffix.lower().lstrip(".")
    tried = []
    info = None
    for name, env, fn in (("ffprobe", ENV_FFPROBE, _probe_ffprobe), ("ffmpeg", ENV_FFMPEG, _probe_ffmpeg)):
        b = _find_bin(name, env)
        if not b:
            tried.append(f"{name} が見つからない")
            continue
        try:
            info = fn(b, p)
            break
        except (ValueError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as ex:
            tried.append(f"{name}: {ex}")
    if info is None and ext in ("mp4", "m4v", "mov", "qt", "3gp"):
        info = _probe_mp4(p)
    if info is None:
        raise ValueError("動画を測れなかった（" + "、".join(tried) + "）。duration_sec・width・height を数字で渡す")
    if tried:
        info["測れなかった道具"] = tried
    w, h = info.get("幅"), info.get("高さ")
    if w and h and abs(info.get("回転") or 0) in (90, 270):
        info["幅"], info["高さ"] = h, w
        info["注記"] = f"回転 {info['回転']} 度の指定があるので、幅と高さを入れ替えた（見た目の向き）"
    info["ファイル"] = str(p)
    info["拡張子"] = ext
    info["容量（MB）"] = round(p.stat().st_size / 1024 ** 2, 2)
    info["容量（バイト）"] = p.stat().st_size
    return info


# ================= 照らす =================

def _cites(refs) -> List[dict]:
    return [kb.cite(r[0], r[1], expect=r[2]) for r in refs]


def _pick_formats(formats: Optional[List[str]]) -> Optional[set]:
    if not formats:
        return None
    wanted = set()
    for f in formats:
        nf = re.sub(r"[\s_\-・（）()]+", "", unicodedata.normalize("NFKC", str(f)).casefold())
        hits = [s["fmt"] for s in SLOTS if nf and nf in re.sub(r"[\s_\-・（）()]+", "", s["fmt"].casefold())]
        for k, v in FORMAT_ALIASES.items():
            if nf == re.sub(r"[\s_\-]+", "", k.casefold()) or (len(nf) >= 3 and nf in re.sub(r"[\s_\-]+", "", k.casefold())):
                hits += [s["fmt"] for s in SLOTS if s["fmt"].startswith(v)]
        if not hits:
            raise ValueError(f"フォーマットは {' / '.join(s['fmt'] for s in SLOTS)} のどれか（受け取った値: {f!r}）")
        wanted.update(hits)
    return wanted


def _common(w: int, h: int, ratio: str, orient: str, size_bytes: Optional[int], ftype: Optional[str],
            has_video: bool, audio: Optional[bool]) -> Tuple[List[dict], List[str]]:
    rows, findings = [], []

    def row(item, verdict, detail, refs):
        rows.append({"項目": item, "判定": verdict, "中身": detail, "根拠": _cites(refs)})
        if verdict in ("合わない", "要確認"):
            findings.append(f"{item}: {detail}")

    if ftype is None:
        row("形式", "未確認", "形式が分からないので見ていない", [(KB, "V-01", ("MP4", "WebM"))])
    elif ftype in AUDIO_ONLY:
        row("形式", "合わない", f"{ftype} は音声だけのファイル。映像が要る", [(KB, "V-03", ("MP3",))])
    else:
        name = ACCEPTED_TYPES.get(ftype, "?")
        if name:
            row("形式", "合う" if name != "?" else "未確認",
                f"{name}（受け付ける形式の一覧にある）" if name != "?" else f"{ftype} は受け付ける形式の一覧と照らせなかった",
                [(KB, "V-01", ("MP4", "WebM", "MOV"))])
        else:
            row("形式", "合わない", f"{ftype} は受け付ける形式の一覧に無い（MP4・MOV・WebM などで書き出す）", [(KB, "V-01", ("MP4",))])
    if not has_video:
        row("映像", "合わない", "映像の流れが見つからない", [(KB, "V-03", ("音声だけ",))])
    if size_bytes is None:
        row("容量", "未確認", "容量を渡していないので見ていない（上限 256 GB）", [(KB, "V-02", ("256 GB",))])
    else:
        row("容量", "合う" if size_bytes < MAX_BYTES else "合わない", f"{size_bytes / 1024 ** 2:,.1f} MB（上限 256 GB 未満）",
            [(KB, "V-02", ("256 GB",))])
    if ratio in RECOMMENDED_RATIOS:
        row("縦横比", "合う", f"{ratio}（推奨の比率）", [(KB, "V-04", ("16:9", "9:16", "1:1"))])
    elif ratio != "その他":
        row("縦横比", "合う", f"{ratio}（受け付けるが推奨ではない。推奨は 16:9・9:16・1:1）", [(KB, "V-04", ("4:5", "2:3"))])
    else:
        row("縦横比", "要確認", f"{w}×{h}（{w / h:.3f}）は一覧のどの比率にも当たらない（±1% で見た）。黒帯が付くか断られる可能性",
            [(KB, "V-04", ("16:9",))])
    tiers = RES_TIERS.get(orient if ratio != "その他" else "", None) if ratio in ("16:9", "9:16", "1:1") else None
    if tiers:
        rec, hd, sd = tiers
        if w >= rec[0] and h >= rec[1]:
            row("解像度", "合う", f"{w}×{h}（推奨の 1080p 以上）", [(KB, "V-05", ("1920×1080",))])
        elif w >= hd[0] and h >= hd[1]:
            row("解像度", "要確認", f"{w}×{h}（HD の最小 {hd[0]}×{hd[1]} 以上だが、推奨の {rec[0]}×{rec[1]} 未満）", [(KB, "V-06", ("1280×720",))])
        elif w >= sd[0] and h >= sd[1]:
            row("解像度", "要確認", f"{w}×{h}（SD。公式は SD を推奨しない）", [(KB, "V-07", ("640×480",))])
        else:
            row("解像度", "合わない", f"{w}×{h}（SD の最小 {sd[0]}×{sd[1]} 未満。受け付けられない可能性。39 の V-07 は推測を含む）",
                [(KB, "V-07", ("640×480",))])
    else:
        row("解像度", "未確認", f"{w}×{h}。{ratio} の比率の推奨・最小の解像度は知識ベースに無い", [(KB, "V-05", ("1080p",))])
    if audio is None:
        rows.append({"項目": "音声", "判定": "未確認", "中身": "音声の有無を渡していない", "根拠": []})
    elif not audio:
        rows.append({"項目": "音声", "判定": "参考", "中身": "音声が無い（無音）。仕様の決まりではない。38 の ABCD は音を使うことを勧める（kb_video_guide で引く）",
                     "根拠": []})
    return rows, findings


def _slot(slot: dict, dur: Optional[float], ratio: str, orient: str) -> dict:
    problems, notes = [], []
    verdict_extra = None
    if slot.get("ratios") and ratio not in slot["ratios"]:
        problems.append(f"比率 {ratio} はこの枠の比率（{'・'.join(slot['ratios'])}）に無い")
    if slot.get("must_horizontal") and orient != "横":
        problems.append("この枠は横長でなければならない")
    if slot.get("rec_ratios") and ratio not in slot["rec_ratios"] and not problems:
        notes.append(f"この枠の推奨の向きは {'・'.join(slot['rec_ratios'])}")
    if dur is None:
        notes.append("長さが分からないので、長さは見ていない")
    else:
        mn, mx, alt = slot.get("min"), slot.get("max"), slot.get("alt_max")
        if mn is None and mx is None and not slot.get("rec_max") and not slot.get("rec_min") and not slot.get("rec"):
            pass
        if mn is not None and dur < mn - EPS:
            problems.append(f"短すぎる（{dur:.2f} 秒。{mn} 秒以上）")
        if mx is not None and dur > mx + EPS:
            problems.append(f"長すぎる（{dur:.2f} 秒。{mx} 秒以下）")
        elif alt is not None and dur > alt + EPS:
            verdict_extra = f"英語版の上限（{mx} 秒）なら合うが、日本語版の上限（{alt} 秒）だと超過"
        if slot.get("rec_max") and dur >= slot["rec_max"]:
            notes.append(f"{slot['rec_max']} 秒未満を推奨（{dur:.1f} 秒）")
        if slot.get("rec_min") and dur <= slot["rec_min"]:
            notes.append(f"{slot['rec_min']} 秒超を推奨（{dur:.1f} 秒）")
        if slot.get("rec") and abs(dur - slot["rec"]) > 0.5:
            notes.append(f"推奨は {slot['rec']} 秒（{dur:.1f} 秒）")
        if slot.get("instream_min") and dur < slot["instream_min"] and not problems:
            notes.append(f"{slot['instream_min']} 秒未満なので YouTube のインストリームには出ない（インフィード・ショートなどには出る）")
        if mn is None:
            notes.append("長さの下限は知識ベースで未確認")
    if slot.get("note"):
        notes.append(slot["note"])
    if slot.get("conflict"):
        notes.append(slot["conflict"])
    notes = list(dict.fromkeys(notes))
    verdict = "合わない" if problems else ("どちらとも言えない（公式の中で食い違う）" if verdict_extra else "合う")
    if verdict_extra:
        notes.insert(0, verdict_extra)
    return {"フォーマット": slot["fmt"], "判定": verdict, "理由": problems or None, "注記": notes or None,
            "根拠": _cites(slot["refs"])}


def video_spec_check(path: str = "", duration_sec: Optional[float] = None, width: Optional[int] = None,
                     height: Optional[int] = None, size_mb: Optional[float] = None, file_format: str = "",
                     has_audio: Optional[bool] = None, formats: Optional[List[str]] = None) -> dict:
    measured = None
    if path:
        measured = measure(path)
        duration_sec = measured["長さ（秒）"] if duration_sec is None else duration_sec
        width, height = measured["幅"], measured["高さ"]
        size_bytes = measured["容量（バイト）"]
        file_format = file_format or measured["拡張子"]
        has_audio = measured["音声"] if has_audio is None else has_audio
    else:
        size_bytes = None if size_mb in (None, "") else int(float(size_mb) * 1024 ** 2)  # type: ignore[arg-type]
    try:
        w, h = int(width), int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("width と height を数字（px）で渡すか、path で動画のファイルを渡す") from None
    if w <= 0 or h <= 0:
        raise ValueError("幅と高さは 1 以上の数字で渡す")
    dur = None if duration_sec in (None, "") else float(duration_sec)  # type: ignore[arg-type]
    if dur is not None and dur <= 0:
        raise ValueError("長さ（duration_sec）は 0 より大きい秒数で渡す")
    ftype = str(file_format).strip().lower().lstrip(".") or None
    ratio, orient = _ratio_name(w, h)
    wanted = _pick_formats(formats)
    common, findings = _common(w, h, ratio, orient, size_bytes, ftype, bool(measured is None or measured.get("幅")), has_audio)
    slots = [_slot(s, dur, ratio, orient) for s in SLOTS if not wanted or s["fmt"] in wanted]
    combos = []
    if dur is not None:
        for c in COMBOS:
            if c["orient"] == orient and c["lo"] - EPS <= dur <= c["hi"] + EPS:
                combos.append({"キャンペーン": c["campaign"], "推奨の 1 本": c["piece"], "根拠": kb.cite(c["ref"][0], c["ref"][1], expect=c["ref"][2])})
    fits = [s["フォーマット"] for s in slots if s["判定"] == "合う"]
    summary = findings[:]
    summary.append(("合う枠: " + "、".join(fits)) if fits else "どの枠にも合わない（理由は「フォーマットごと」）")
    return {
        "対象アカウント": "なし（計算）",
        "期間": "指定なし",
        "単位": "長さは秒、寸法は px、容量は MB（1 MB＝1,048,576 バイト）",
        "入力": {"長さ（秒）": dur, "幅": w, "高さ": h, "比率": ratio, "向き": orient, "形式": ftype, "音声": has_audio,
                "フォーマットの絞り込み": sorted(wanted) if wanted else "指定なし（全部）"},
        "測った結果": measured,
        "所見": summary,
        "共通の決まり": common,
        "フォーマットごと": slots,
        "推奨の組み合わせのどれに当たるか": combos or ("長さが分からない" if dur is None else "どの推奨の 1 本にも当たらない"),
        "推奨の組み合わせの見方": COMBO_NOTE,
        "比率の許容": "±1%（画像の決まり 15 の 1-68 と同じ幅。動画の許容幅は知識ベースに無い）",
        "点検の範囲": "長さ・縦横比・解像度・容量・形式・音声の有無だけ。中身（ブランドの出し方、文字の読みやすさ、権利、ポリシー）は見ていない。"
                  "中身の観点は video_storyboard_check（ABCD の問い）で確かめる。YouTube に上げたときの処理や審査の結果は保証しない。",
    }
