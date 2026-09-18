"""pytest 全局配置：在导入 app 前固定测试数据目录。"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / ".testdata"
os.environ["LG_DATA_DIR"] = str(TEST_DATA)
os.environ.setdefault("LG_FFMPEG", "/opt/homebrew/bin/ffmpeg")
os.environ.setdefault("LG_FFPROBE", "/opt/homebrew/bin/ffprobe")


def pytest_sessionstart(session):
    # 清理上一轮测试自建的临时数据（仅 .testdata，属任务临时产物）
    if TEST_DATA.exists():
        shutil.rmtree(TEST_DATA)
