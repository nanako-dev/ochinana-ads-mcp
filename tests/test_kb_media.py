"""知識ベースを媒体を指定して引く（Meta・LINEヤフー・ChatGPT）。小さな見本のフォルダと、あれば実物で確かめる。"""
import pytest

from ochinana_ads import kb, quiz, server


@pytest.fixture()
def media(tmp_path, monkeypatch):
    base = tmp_path / "媒体仕様メモ"
    g = base / "Google広告の基礎"
    g.mkdir(parents=True)
    (g / "00_全体像.md").write_text("# Google の全体像\n\n- 確認日: 2026-11-20\n\n## 1. 入札\n\nGoogle の入札の話。\n", encoding="utf-8")
    (base / "Google_横断.md").write_text("# 横断のメモ\n\nGoogle だけで読む。\n", encoding="utf-8")
    m = base / "Meta広告の基礎"
    m.mkdir()
    (m / "00_全体像.md").write_text("# Meta 広告の全体像\n\n- 確認日: 2026-11-18\n\n## 1. Advantage+\n\nAdvantage+ のキャンペーン。\n", encoding="utf-8")
    (m / "90_核となる理解.md").write_text("# 核となる理解（Meta）\n\n- 確認日: 2026-11-19\n\n学習期間は 50 件。\n", encoding="utf-8")
    (m / "_下書き.md").write_text("# 読まない\n", encoding="utf-8")
    (m / "quiz.jsonl").write_text('{"id": "M-001", "分野": "入札", "問い": "Meta の学習期間は？", "正解": "50 件"}\n', encoding="utf-8")
    monkeypatch.setenv(kb.ENV_KB_DIR, str(g))
    return base


def test_媒体の名前は日本語や旧称でも決まる():
    assert kb.media_key("") == "google"
    assert kb.media_key("Facebook") == "meta"
    assert kb.media_key("Yahoo!広告") == "lineyahoo"
    assert kb.media_key("ＬＩＮＥヤフー") == "lineyahoo"
    assert kb.media_key("OpenAI") == "chatgpt"
    with pytest.raises(kb.KBError, match="meta"):
        kb.media_key("tiktok")


def test_媒体を指定すると_その媒体のフォルダだけを引く(media):
    with kb.use_media("meta"):
        out = kb.overview()
        assert {r["番号"] for r in out["行"]} == {"00", "90"}  # 1 つ上の横断のメモと _下書き は入れない
        assert out["媒体"] == "Meta 広告"
        assert [f["ファイル"] for f in out["最初に読むもの"]] == ["90_核となる理解.md", "00_全体像.md"]
        assert "media='meta'" in out["最初に読むもの"][0]["読むには"]
        hits = kb.search("Advantage+")
        assert hits["行"] and all("Meta" in h["ファイル"] or h["ファイル"].startswith("00") for h in hits["行"])
    assert kb.current_media() == "google"  # 抜けたら Google に戻る
    assert "Google_横断" in {r["番号"] for r in kb.overview()["行"]}
    assert not kb.search("Advantage+")["行"]


def test_章が増えても_呼ぶたびに拾う(media):
    with kb.use_media("meta"):
        n = len(kb.overview()["行"])
        (media / "Meta広告の基礎" / "41_新しい章.md").write_text("# 新しい章\n\n足した章。\n", encoding="utf-8")
        assert len(kb.overview()["行"]) == n + 1


def test_引ける媒体の一覧(media):
    rows = {r["指定"]: r for r in kb.media_list()}
    assert rows["meta"]["ある"] and rows["meta"]["メモの数"] == 2
    assert not rows["chatgpt"]["ある"]
    with kb.use_media("chatgpt"), pytest.raises(kb.KBError, match="ChatGPT"):
        kb.overview()


def test_媒体ごとに場所を変えられる(media, tmp_path, monkeypatch):
    other = tmp_path / "別の場所"
    other.mkdir()
    (other / "00_全体像.md").write_text("# ChatGPT の広告\n", encoding="utf-8")
    monkeypatch.setenv(kb.ENV_KB_DIR + "_CHATGPT", str(other))
    with kb.use_media("chatgpt"):
        assert [r["番号"] for r in kb.overview()["行"]] == ["00"]


def test_道具の入口でも媒体を渡せる(media):
    out = server.kb_read.__wrapped__(file="90", media="meta") if hasattr(server.kb_read, "__wrapped__") else server.kb_read(file="90", media="meta")
    assert "50 件" in str(out)
    q = quiz.quiz_rows(media / "Meta広告の基礎")
    assert q and q["rows"][0]["id"] == "M-001"


REAL = kb.media_dir("meta")


@pytest.mark.skipif(not (REAL and REAL.is_dir()), reason="実物の他媒体の知識ベースが無い環境")
@pytest.mark.parametrize("m", ["meta", "lineyahoo", "chatgpt"])
def test_実物の他媒体を読める(m, monkeypatch):
    monkeypatch.delenv(kb.ENV_KB_DIR, raising=False)
    with kb.use_media(m):
        out = kb.overview()
        names = {r["ファイル"] for r in out["行"]}
        assert any(n.startswith("00_") for n in names) and any(n.startswith("28_") for n in names)
        assert kb.rules("入札")["行"]
        assert kb.read("00", toc_only=True)


def test_ほかの道具も媒体を渡せて_無い種類は無いと返す(media):
    (media / "Meta広告の基礎" / "ロジックツリー").mkdir()
    with kb.use_media("meta"):
        with pytest.raises(kb.KBError, match="Meta 広告にはまだ無い"):
            kb.glossary("CPA")
        with pytest.raises(kb.KBError, match="Meta 広告にはまだ無い"):
            kb.tips("入札")
        from ochinana_ads import kata
        with pytest.raises(kb.KBError, match="Meta 広告にはまだ無い"):
            kata.kata("規換")
    for name in ("kb_logic_tree", "kb_glossary", "kb_tips", "kb_kata"):
        import inspect
        assert "media" in inspect.signature(getattr(server, name)).parameters
