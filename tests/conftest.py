"""テストの共通の準備。口座 ID は作りもの。本物の API・知識ベース・ファイルには触らない。"""
import os
from pathlib import Path

import pytest

# 許可リストは作りものの 2 つ（config は読み込むときに環境変数を見るので、先に置く）
os.environ["ADS_ALLOWED_CUSTOMER_IDS"] = "1112223333,4445556666"  # 手元の本物の設定があっても、テストは作りものを使う

# 知識ベース: ADS_KB_DIR か、同梱の kb/Google広告の基礎
KB_DIR = os.environ.get("ADS_KB_DIR") or str(Path(__file__).resolve().parent.parent / "kb" / "Google広告の基礎")
KB_READY = Path(KB_DIR).is_dir()


def pytest_collection_modifyitems(config, items):
    """知識ベース（ADS_KB_DIR）が無いと確かめられないテストは、理由をつけて飛ばす。"""
    if KB_READY:
        return
    skip = pytest.mark.skip(reason="知識ベースが無い（ADS_KB_DIR で知識ベースのフォルダを指すと走る）")
    for item in items:
        if (item.module.__name__.split(".")[-1] in KB_MODULES
                or any(w in item.name for w in KB_TEST_WORDS)):
            item.add_marker(skip)


# 知識ベースの中身を確かめるテストのファイル
KB_MODULES = {"test_kb", "test_kb_kata", "test_kb_rules_csv"}
# 道具の根拠の行が、いまの知識ベースにあるかを確かめるテスト（名前に「実物」と「知識ベース」が入る）
KB_TEST_WORDS = ("いまも知識ベースにある",)
