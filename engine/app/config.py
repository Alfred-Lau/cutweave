"""全局配置与字体探测。

环境变量可覆盖：
- LG_DATA_DIR   数据根目录（草稿/素材/成片）
- LG_FFMPEG     ffmpeg 可执行文件路径
- LG_FFPROBE    ffprobe 可执行文件路径
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # engine/
DATA_DIR = Path(os.environ.get("LG_DATA_DIR", BASE_DIR / "data"))
DRAFTS_DIR = DATA_DIR / "drafts"
ASSETS_DIR = DATA_DIR / "assets"
CACHE_DIR = ASSETS_DIR / "cache"
RENDERS_DIR = DATA_DIR / "renders"
TTS_DIR = DATA_DIR / "tts"

FFMPEG = os.environ.get("LG_FFMPEG", "/opt/homebrew/bin/ffmpeg")
FFPROBE = os.environ.get("LG_FFPROBE", "/opt/homebrew/bin/ffprobe")

# drawtext 用的中文字体候选（按顺序探测；覆盖 macOS 与 Debian/Ubuntu 容器）
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Debian fonts-noto-cjk
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",   # 部分发行版路径
]

API_PREFIX = "/api/v1"


def ensure_dirs() -> None:
    for d in (DRAFTS_DIR, ASSETS_DIR, CACHE_DIR, RENDERS_DIR, TTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def detect_font() -> str | None:
    """返回第一个存在的可用字体路径；找不到返回 None。"""
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


DEFAULT_FONT = detect_font()
