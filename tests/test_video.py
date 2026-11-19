"""動画の道具（2026-11-18）: 仕様の点検、動画広告の文字数、ABCD の指針、台本の見直し。外にも API にもつながない。

ふだんは小さな見本の知識ベース（38 と 39 の数行）で確かめる。最後に、実物の知識ベースがあれば、
道具が根拠にしている行が全部いまもあって数字が合うかを確かめる。
"""
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from ochinana_ads import abcd, kb, specs, video

MINI_38 = """# 38 動画のクリエイティブ（見本）

- 確認日: 2026-11-18

## 1. 中核

| 番号 | 原則 | 指針（公式の日本語名） | 中身 | 目的 | フォーマット | 尺 | 確認の問い | 印 | 出典 |
|---|---|---|---|---|---|---|---|---|---|
| ABCD-A1 | A 注目を集める | ストーリーに飛び込む | 本題から入る | 全部 | 全部 | 全部 | 最初の 1〜2 秒に本題の画があるか | ✅ | S2 |
| ABCD-B1 | B ブランディング | 最初から最後までブランドを提示する | 冒頭から | 全部 | 全部 | 全部 | 最初の 5 秒にブランドが出るか | ✅ | S2 |
| ABCD-D1 | D 行動を促す | 行動を促すフレーズ（CTA）を含める | 頼む | 全部 | 全部 | 全部 | CTA があるか | ✅ | S2 |
| 認知-B | B ブランディング | ブランドを前面に押し出す | 回数を増やす | 認知 | 全部 | 全部 | 何回出るか | ✅ | S2 |
| 行動-D | D 行動を促す | コンテキストを示して行動を促す | あとで CTA | 行動 | 全部 | 全部 | CTA は後か | △ | S2 |
| ショート-D | D 行動を促す | 導く | CTA で終える | 全部 | ショート | 全部 | 最後に CTA | ✅ | S24 |
| バンパー-3 | D 行動を促す | 締めに約 2 秒 | 2 秒 | 全部 | バンパー | 〜6 秒 | 締めに 2 秒あるか | ✅ | S33 |
| 動画再生回数-1 | C つながり | 30 秒超で物語を | 物語 | 比較検討 | 動画再生回数 | 30 秒超 | 30 秒超の版はあるか | ✅ | S1 |
| G-01 | 全体 | クリエイティブの属性 | 4 つ | 全部 | 動画リーチ・動画再生回数 | 全部 | 4 属性を満たすか | ✅ | S5 |

## 6. 数字

| 番号 | 中身 | 数字 | 誰が・いつ・どれだけで測ったか | 出典 | 印 |
|---|---|---|---|---|---|
| N-01 | ABCD | +30% | Google と Kantar、2021、11,000 本 | S2 | △ |
"""


MINI_40 = """# 40 動画広告の作り方（見本）

- 確認日: 2026-11-18

## 1. 企画

| 番号 | 工程 | 項目 | やること | 目的 | フォーマット | 尺 | 確認の問い | 印 | 出典 |
|---|---|---|---|---|---|---|---|---|---|
| 作-企画-01 | 企画 | 向きと尺を先に決める | 横・縦・正方形と 6・15・30 秒を計画に入れる | 全部 | 全部 | 全部 | 向きと尺の一覧を作ったか | ✅ | P1 |
| 作-企画-02 | 企画 | ショートは専用に作る | 縦の版を別に作る | 全部 | ショート | 全部 | ショート用に作り直したか | ✅ | P2 |

## 2. 音

| 番号 | 工程 | 項目 | やること | 目的 | フォーマット | 尺 | 確認の問い | 印 | 出典 |
|---|---|---|---|---|---|---|---|---|---|
| 作-音-01 | 音 | 曲の権利 | 権利を持つ曲だけを使う | 全部 | 全部 | 全部 | 曲の権利を確かめたか | ✅ | P3 |
"""


@pytest.fixture
def mini_kb(monkeypatch, tmp_path):
    d = tmp_path / "kb"
    d.mkdir()
    (d / "38_動画のクリエイティブ_見本.md").write_text(MINI_38, encoding="utf-8")
    monkeypatch.setenv(kb.ENV_KB_DIR, str(d))
    return d


@pytest.fixture
def mini_kb40(mini_kb):
    (mini_kb / "40_動画広告の作り方_見本.md").write_text(MINI_40, encoding="utf-8")
    return mini_kb


@pytest.fixture
def no_kb(monkeypatch, tmp_path):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(tmp_path / "no-kb"))


@pytest.fixture
def no_tools(monkeypatch):
    """ffprobe・ffmpeg が無い状態（MP4 の箱を自分で読む経路）。"""
    monkeypatch.setenv(video.ENV_FFPROBE, "/nonexistent/ffprobe")
    monkeypatch.setenv(video.ENV_FFMPEG, "/nonexistent/ffmpeg")


def slot(out, name):
    return next(s for s in out["フォーマットごと"] if s["フォーマット"].startswith(name))


# ---------------- video_spec_check（数字で渡す） ----------------

def test_6秒の横はバンパーに合いP_MAXには短い(no_kb):
    out = video.video_spec_check(duration_sec=6, width=1920, height=1080, size_mb=3, file_format="mp4", has_audio=False)
    assert out["入力"]["比率"] == "16:9" and out["入力"]["向き"] == "横"
    assert slot(out, "バンパー")["判定"] == "合う"
    assert slot(out, "P-MAX")["判定"] == "合わない" and "短すぎる" in slot(out, "P-MAX")["理由"][0]
    assert slot(out, "スキップ不可（15")["判定"] == "合わない"
    dg = slot(out, "デマンド")
    assert dg["判定"] == "合う" and any("インストリームには出ない" in n for n in dg["注記"])
    pieces = {(c["キャンペーン"], c["推奨の 1 本"]) for c in out["推奨の組み合わせのどれに当たるか"]}
    assert ("動画リーチ（効率的なリーチ）", "横 6 秒") in pieces
    common = {r["項目"]: r for r in out["共通の決まり"]}
    assert common["解像度"]["判定"] == "合う" and common["形式"]["判定"] == "合う" and common["容量"]["判定"] == "合う"
    assert common["音声"]["判定"] == "参考"
    # 知識ベースが無くても動き、根拠は「読めなかった」と返る
    assert "読めなかった" in json.dumps(common["形式"]["根拠"], ensure_ascii=False)


def test_6秒ちょうどを少しでも超えるとバンパーに合わない(no_kb):
    assert slot(video.video_spec_check(duration_sec=6.0004, width=1920, height=1080), "バンパー")["判定"] == "合う"
    assert slot(video.video_spec_check(duration_sec=6.2, width=1920, height=1080), "バンパー")["判定"] == "合わない"
    assert slot(video.video_spec_check(duration_sec=4.5, width=1920, height=1080), "バンパー")["判定"] == "合わない"


def test_5秒未満はショートにも合わない_管理画面での確認の結果(no_kb):
    out = video.video_spec_check(duration_sec=4, width=1080, height=1920)
    assert slot(out, "ショート")["判定"] == "合わない" and "短すぎる" in slot(out, "ショート")["理由"][0]
    assert slot(video.video_spec_check(duration_sec=6, width=1080, height=1920), "バンパー")["判定"] == "合う"  # 縦のバンパー


def test_公式の中で食い違う上限は両方で判定する(no_kb):
    tf = slot(video.video_spec_check(duration_sec=18, width=1920, height=1080), "スキップ不可（フリークエンシー")
    assert tf["判定"].startswith("どちらとも言えない") and "日本語版" in tf["注記"][0]
    assert slot(video.video_spec_check(duration_sec=12, width=1920, height=1080), "スキップ不可（フリークエンシー")["判定"] == "合う"
    assert slot(video.video_spec_check(duration_sec=22, width=1920, height=1080), "スキップ不可（フリークエンシー")["判定"] == "合わない"


def test_縦の動画は横長が必須の枠に合わない(no_kb):
    out = video.video_spec_check(duration_sec=30, width=1080, height=1920)
    assert out["入力"]["向き"] == "縦"
    assert "横長" in " ".join(slot(out, "スキップ不可（30")["理由"])
    assert slot(out, "マストヘッド")["判定"] == "合わない"
    assert slot(out, "ショート")["判定"] == "合う"
    pieces = {c["推奨の 1 本"] for c in out["推奨の組み合わせのどれに当たるか"]}
    assert {"縦 6〜60 秒", "縦 10〜60 秒"} <= pieces


def test_解像度と比率と形式(no_kb):
    rows = lambda o: {r["項目"]: r for r in o["共通の決まり"]}  # noqa: E731
    assert rows(video.video_spec_check(duration_sec=15, width=1280, height=720))["解像度"]["判定"] == "要確認"
    assert rows(video.video_spec_check(duration_sec=15, width=640, height=360))["解像度"]["判定"] == "合わない"
    odd = video.video_spec_check(duration_sec=15, width=2000, height=1000)
    assert odd["入力"]["比率"] == "その他" and rows(odd)["縦横比"]["判定"] == "要確認"
    assert rows(video.video_spec_check(duration_sec=15, width=1080, height=1350))["縦横比"]["判定"] == "合う"  # 4:5 は受け付ける
    assert rows(video.video_spec_check(duration_sec=15, width=1920, height=1080, file_format="mp3"))["形式"]["判定"] == "合わない"
    assert rows(video.video_spec_check(duration_sec=15, width=1920, height=1080, file_format="mkv"))["形式"]["判定"] == "合わない"
    assert rows(video.video_spec_check(duration_sec=15, width=1920, height=1080))["形式"]["判定"] == "未確認"


def test_フォーマットで絞る_と_入力の誤り(no_kb):
    out = video.video_spec_check(duration_sec=6, width=1920, height=1080, formats=["bumper", "ショート"])
    assert [s["フォーマット"] for s in out["フォーマットごと"]] == ["バンパー", "ショート"]
    with pytest.raises(ValueError):
        video.video_spec_check(duration_sec=6, width=1920, height=1080, formats=["テレビCM"])
    with pytest.raises(ValueError):
        video.video_spec_check(duration_sec=6)
    with pytest.raises(ValueError):
        video.video_spec_check(duration_sec=-1, width=10, height=10)
    with pytest.raises(ValueError):
        video.video_spec_check(path="/nonexistent/x.mp4")


# ---------------- ファイルを測る ----------------

def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _mini_mp4(w: int, h: int, seconds: float, audio: bool = True, rotate: int = 0) -> bytes:
    ts = 1000
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", ts, int(seconds * ts)) + b"\x00" * 80)

    def trak(kind: bytes, tw: int, th: int, codec: bytes) -> bytes:
        a, b = (0, 0x10000) if rotate == 90 else (0x10000, 0)
        matrix = struct.pack(">iii", a, b, 0) + struct.pack(">iii", -b, a, 0) + struct.pack(">iii", 0, 0, 0x40000000)
        tkhd = _box(b"tkhd", b"\x00\x00\x00\x07" + b"\x00" * 32 + b"\x00" * 4 + matrix + struct.pack(">II", tw << 16, th << 16))
        hdlr = _box(b"hdlr", b"\x00" * 8 + kind + b"\x00" * 12 + b"x\x00")
        stsd = _box(b"stsd", b"\x00" * 8 + struct.pack(">I", 16) + codec + b"\x00" * 8)
        minf = _box(b"minf", _box(b"stbl", stsd))
        return _box(b"trak", tkhd + _box(b"mdia", hdlr + minf))

    moov = _box(b"moov", mvhd + trak(b"vide", w, h, b"avc1") + (trak(b"soun", 0, 0, b"mp4a") if audio else b""))
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + moov + _box(b"mdat", b"\x00" * 16)


def test_MP4の箱を自分で読む(no_kb, no_tools, tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(_mini_mp4(1080, 1920, 15.0))
    out = video.video_spec_check(path=str(p))
    m = out["測った結果"]
    assert m["測り方"].startswith("MP4・MOV の箱を自分で読んで") and m["長さ（秒）"] == 15.0
    assert (m["幅"], m["高さ"], m["音声"], m["映像の形式"]) == (1080, 1920, True, "avc1")
    assert out["入力"]["向き"] == "縦" and slot(out, "ショート")["判定"] == "合う"
    assert len(m["測れなかった道具"]) == 2
    # 回転の指定があれば、見た目の向きにそろえる
    q = tmp_path / "r.mov"
    q.write_bytes(_mini_mp4(1920, 1080, 6.0, audio=False, rotate=90))
    m2 = video.measure(str(q))
    assert (m2["幅"], m2["高さ"], m2["音声"]) == (1080, 1920, False) and "入れ替えた" in m2["注記"]


def test_読めないファイルは理由つきで断る(no_kb, no_tools, tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="moov"):
        video.measure(str(p))
    q = tmp_path / "x.avi"
    q.write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="duration_sec"):
        video.measure(str(q))


FFMPEG = video._find_bin("ffmpeg", "__NO_ENV__")


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg が無い環境")
def test_手元のffmpegで作った動画を測る(no_kb, tmp_path):
    p = tmp_path / "b.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=1920x1080:d=6:r=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True, timeout=60)
    out = video.video_spec_check(path=str(p))
    m = out["測った結果"]
    assert abs(m["長さ（秒）"] - 6.0) < 0.05 and (m["幅"], m["高さ"]) == (1920, 1080) and m["音声"] is False
    assert m["フレームレート"] == 30 and slot(out, "バンパー")["判定"] == "合う"


# ---------------- ad_text_check の動画の種類 ----------------

def test_動画広告の文字数(no_kb):
    # 管理画面（2026-11-18 の管理画面での確認）: 長い広告見出し 90（必須）、行動を促すフレーズ 10、CTA に付く「広告見出し」30
    out = specs.ad_text_check("動画リーチ", {"長い見出し": ["小さな会社の広告運用"], "CTA": ["詳しくはこちらへ"], "行動を促すフレーズの見出し": ["OCHINANA"]})
    assert out["入力"]["種類"] == "動画リーチ（インストリーム）"
    assert out["入力"]["項目ごとの本数"] == {"長い見出し": 1, "行動を促すフレーズ": 1, "見出し": 1}
    assert any("行動を促すフレーズ 1: 超過" in f for f in out["所見"])  # 全角 8 文字 ＝ 16 ＞ 10
    out = specs.ad_text_check("動画リーチ", {"長い見出し": ["ホッケーの観戦ガイドで試合の見どころがすぐ分かる" * 2], "行動を促すフレーズ": ["相談してみる"]})
    assert any("長い見出し 1: 超過（6 超える）" in f for f in out["所見"]) and any("（12／上限 10）" in f for f in out["所見"])  # 管理画面は 96/90・12/10
    out = specs.ad_text_check("動画リーチ", {"行動を促すフレーズの見出し": ["a"]})
    assert any("行動を促すフレーズ が先に要る" in f for f in out["所見"]) and any("長い見出し: 0 本。最少 1 本" in f for f in out["所見"])
    out = specs.ad_text_check("マストヘッド", {"行動を促すフレーズ": ["今すぐ相談する"]})  # 全角 7 ＝ 14 ≦ 15
    assert not any("超過" in f for f in out["所見"])
    out = specs.ad_text_check("インフィード", {"見出し": ["一", "二", "三"], "説明文": ["あ" * 18]})
    assert any("最大 2 本" in f for f in out["所見"]) and any("説明文 1: 超過" in f for f in out["所見"])
    out = specs.ad_text_check("dg動画", {"見出し": ["a"], "長い見出し": ["b"], "説明文": ["c"], "ビジネスの名前": ["OCHINANA"]})
    assert out["入力"]["種類"] == "デマンド ジェネレーション（動画広告）"
    assert sum("3 本以上" in f for f in out["所見"]) == 3
    out = specs.ad_text_check("shorts", {"見出し": ["あ" * 25]})  # ショートの「見出し」は長い見出しとして数える
    assert any("推奨の 半角 40" in f for f in out["所見"]) and not any("超過" in f for f in out["所見"])
    # 以前の種類は変わらない
    assert specs.ad_text_check("dg", {"見出し": ["a"]})["入力"]["種類"] == "デマンド ジェネレーション"


# ---------------- kb_video_guide ----------------

def test_指針を目的とフォーマットと尺で絞る(mini_kb):
    out = abcd.guide(objective="認知", format="バンパー", length_sec=6)
    ids = [r["番号"] for r in out["指針"]]
    assert ids == ["ABCD-A1", "ABCD-B1", "ABCD-D1", "認知-B", "バンパー-3"]
    assert out["指針"][0]["確認の問い"].startswith("最初の 1〜2 秒") and out["指針"][0]["根拠"]["行番号"] > 0
    assert out["公式が示す数字"][0]["番号"] == "N-01"
    assert any("2017" in n for n in out["注記"])
    ids = [r["番号"] for r in abcd.guide(objective="行動", format="ショート", letter="D")["指針"]]
    assert ids == ["ABCD-D1", "行動-D", "ショート-D", "G-01"] or ids == ["ABCD-D1", "行動-D", "ショート-D"]
    assert "動画再生回数-1" not in [r["番号"] for r in abcd.guide(format="動画再生回数", length_sec=15)["指針"]]
    assert "動画再生回数-1" in [r["番号"] for r in abcd.guide(format="vvc", length_sec=45)["指針"]]
    assert [r["番号"] for r in abcd.guide(query="ブランド", include_numbers=False)["指針"]] == ["ABCD-B1", "認知-B"]
    with pytest.raises(ValueError):
        abcd.guide(objective="売上")
    with pytest.raises(ValueError):
        abcd.guide(letter="E")


def test_作り方の工程で絞る(mini_kb40):
    out = abcd.guide(phase="企画")
    assert [r["番号"] for r in out["指針"]] == ["作-企画-01", "作-企画-02"]
    assert out["指針"][0]["工程"] == "企画" and out["指針"][0]["項目"] == "向きと尺を先に決める"
    assert out["指針"][0]["確認の問い"] == "向きと尺の一覧を作ったか" and out["工程ごとの件数"] == {"企画": 2}
    assert [r["番号"] for r in abcd.guide(phase="企画", format="バンパー")["指針"]] == ["作-企画-01"]
    assert [r["番号"] for r in abcd.guide(phase="bgm")["指針"]] == ["作-音-01"]
    allrows = abcd.guide(max_results=100, include_numbers=False)
    assert allrows["工程ごとの件数"]["中身（ABCD ほか、38）"] == 9 and allrows["工程ごとの件数"]["音"] == 1
    assert len(allrows["出どころ"]) == 2
    with pytest.raises(ValueError):
        abcd.guide(phase="納品")


def test_40が無くても38だけで動く(mini_kb):
    out = abcd.guide(include_numbers=False)
    assert len(out["指針"]) == 9 and any("40" in n for n in out["注記"])
    assert abcd.guide(phase="企画")["指針"] == []


def test_知識ベースが無ければ理由つきで断る(no_kb):
    with pytest.raises(ValueError, match="知識ベース"):
        abcd.guide()


# ---------------- video_storyboard_check ----------------

SCENES = [
    {"開始": 0, "終了": 1.5, "画": "色面が走り込む。右上に小さく社名", "文字": "広告の数字、読めてる？"},
    {"開始": 1.5, "終了": 4.5, "画": "大きな文字", "文字": "読む、点検する、次の一手を出す。"},
    {"開始": 4.5, "終了": 6, "画": "ロゴと社名 OCHINANA", "文字": "OCHINANA", "行動": "相談してみる"},
]


def test_台本を見直す_判定せず問いを返す(mini_kb):
    out = abcd.storyboard_check(scenes=SCENES, brand_name="OCHINANA", objective="認知", format="バンパー", has_audio=False)
    items = {i["番号"]: i for i in out["中核の ABCD と自動で見られる 4 属性"]}
    assert "無音" in " ".join(items["ABCD-A2"]["見つからなかった目印"])
    assert any("最初の 5 秒" in x for x in items["ABCD-B1"]["見つかった目印"])  # 4.5 秒からのロゴ
    assert any("ブランド名を声で" in x for x in items["ABCD-B2"]["見つからなかった目印"])
    assert any("相談してみる" in x for x in items["ABCD-D1"]["見つかった目印"])
    assert items["ABCD-D1"]["確認の問い"] == "CTA があるか" and items["ABCD-A1"]["根拠"]["行番号"] > 0
    assert "見つからない" in items["ABCD-A2"]["根拠"]  # 見本の 38 に A2 の行は無い → そう返す
    assert [e["番号"] for e in out["目的・フォーマットに合わせた問い"]] == ["認知-B", "バンパー-3"]
    assert "判定はしない" in out["判定の範囲"] and "80%" in out["判定の範囲"]
    assert out["入力"]["尺（秒）"] == 6 and out["入力"]["時刻つき"] is True


def test_台本を文で渡す(mini_kb):
    text = "0-2秒 画: 店員が手を振る 文字: 今日だけ？ 音声: こんにちは、OCHINANA です\n2-5秒 画: 商品の寄り 文字: 軽い\n5-6秒 画: ロゴ 文字: 詳しくはこちら 音声: 詳しくはこちら"
    out = abcd.storyboard_check(script=text, brand_name="OCHINANA", format="ショート", objective="行動")
    assert [s["時刻"] for s in out["読んだ場面"]] == ["0〜2 秒", "2〜5 秒", "5〜6 秒"]
    items = {i["番号"]: i for i in out["中核の ABCD と自動で見られる 4 属性"]}
    assert items["ABCD-B2"]["見つかった目印"] and items["ABCD-D2"]["見つかった目印"] and items["ABCD-C1"]["見つかった目印"]
    assert [e["番号"] for e in out["目的・フォーマットに合わせた問い"]] == ["行動-D", "ショート-D"]
    with pytest.raises(ValueError):
        abcd.storyboard_check(script="台本です", brand_name="X")
    with pytest.raises(ValueError):
        abcd.storyboard_check(scenes=SCENES, brand_name="")


# ---------------- 実物の知識ベースと突き合わせる（あるときだけ） ----------------

REAL = Path(kb.DEFAULT_KB_DIR)


@pytest.mark.skipif(not (REAL / "39_動画の仕様_点検用の表.md").is_file(), reason="実物の知識ベース（38・39）が無い環境")
def test_実物_動画の道具の根拠の行がいまも知識ベースにある(monkeypatch):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(REAL))
    outs = [video.video_spec_check(duration_sec=d, width=w, height=h, size_mb=10, file_format="mp4", has_audio=True)
            for d, w, h in ((6, 1920, 1080), (15, 1920, 1080), (30, 1920, 1080), (90, 1920, 1080), (15, 1080, 1920),
                            (20, 1080, 1080), (12, 1280, 720), (12, 640, 480), (12, 2000, 1000))]
    outs.append(video.video_spec_check(duration_sec=10, width=1920, height=1080, file_format="mp3"))
    cites = []

    def walk(o):
        if isinstance(o, dict):
            if "確かめ" in o:
                cites.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(outs)
    used = {(r[0], r[1]) for s in video.SLOTS for r in s["refs"]} | {(c["ref"][0], c["ref"][1]) for c in video.COMBOS}
    seen = {("39", c["行"]) for c in cites if "行" in c}
    assert used <= seen, used - seen
    broken = [c for c in cites if "見つからない" in c["確かめ"] or "見当たらない" in c["確かめ"] or "読めなかった" in c["確かめ"]]
    assert broken == [], json.dumps(broken, ensure_ascii=False, indent=1)
    # 38: 中核の 11 項目と、台本の見直しが使う G-01 がある
    g = abcd.guide(include_numbers=True, max_results=100, max_chars=200000)
    ids = {r["番号"] for r in g["指針"]}
    assert {f"ABCD-{x}" for x in ("A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3", "D1", "D2")} | {"G-01"} <= ids
    assert len(g["公式が示す数字"]) >= 10
    sb = abcd.storyboard_check(scenes=SCENES, brand_name="OCHINANA", objective="認知", format="バンパー")
    assert all(isinstance(i["根拠"], dict) for i in sb["中核の ABCD と自動で見られる 4 属性"])


@pytest.mark.skipif(not (REAL / "40_動画広告の作り方_企画から入稿と検証まで.md").is_file(), reason="実物の知識ベース（40）が無い環境")
def test_実物_作り方の工程を全部引ける(monkeypatch):
    monkeypatch.setenv(kb.ENV_KB_DIR, str(REAL))
    for phase in abcd.PHASES:
        out = abcd.guide(phase=phase, include_numbers=False, max_results=200, max_chars=300000)
        assert out["指針"], phase
        assert all(r["工程"] == phase and r["確認の問い"] and r["根拠"]["行番号"] > 0 for r in out["指針"]), phase
    whole = abcd.guide(include_numbers=False, max_results=200, max_chars=400000)
    assert set(abcd.PHASES) <= set(whole["工程ごとの件数"]) and whole["工程ごとの件数"]["中身（ABCD ほか、38）"] >= 50
