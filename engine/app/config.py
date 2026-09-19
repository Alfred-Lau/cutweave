"""全局配置与字体探测。

环境变量可覆盖：
- LG_DATA_DIR   数据根目录（草稿/素材/成片）
- LG_FFMPEG     ffmpeg 可执行文件路径
- LG_FFPROBE    ffprobe 可执行文件路径
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # engine/
DATA_DIR = Path(os.environ.get("LG_DATA_DIR", BASE_DIR / "data"))
DRAFTS_DIR = DATA_DIR / "drafts"
ASSETS_DIR = DATA_DIR / "assets"
CACHE_DIR = ASSETS_DIR / "cache"
RENDERS_DIR = DATA_DIR / "renders"
TTS_DIR = DATA_DIR / "tts"

# 优先环境变量 → PATH 探测（跨平台：macOS homebrew / Linux /usr/bin）→ homebrew 兜底
FFMPEG = (
    os.environ.get("LG_FFMPEG")
    or shutil.which("ffmpeg")
    or "/opt/homebrew/bin/ffmpeg"
)
FFPROBE = (
    os.environ.get("LG_FFPROBE")
    or shutil.which("ffprobe")
    or "/opt/homebrew/bin/ffprobe"
)

# drawtext 用的中文字体候选（按顺序探测；覆盖 macOS 与 Debian/Ubuntu 容器）
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Debian fonts-noto-cjk
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",   # 部分发行版路径
]

# ---- 火山引擎豆包语音（大模型语音合成；复刻音色 S_ 声音ID 直接当 voice 用）----
VOLC_TTS_APPID = os.environ.get("VOLC_TTS_APPID", "")
VOLC_TTS_TOKEN = os.environ.get("VOLC_TTS_TOKEN", "")
VOLC_TTS_CLUSTER = os.environ.get("LG_VOLC_CLUSTER", "volcano_tts")
VOLC_TTS_VOICE = os.environ.get("LG_VOLC_VOICE", "")  # 缺省音色（官方音色名或复刻声音ID）

API_PREFIX = "/api/v1"

# ---- AI 语音（阿里百炼 DashScope：CosyVoice 合成/声音复刻）----
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_WORKSPACE_ID = os.environ.get("DASHSCOPE_WORKSPACE_ID", "")  # 默认业务空间可留空
COSYVOICE_MODEL = os.environ.get("LG_COSYVOICE_MODEL", "cosyvoice-v3-flash")

# ---- OSS 中转（声音复刻的参考音频需公网 URL；ECS 内网 endpoint 上传免流量费）----
OSS_BUCKET = os.environ.get("OSS_BUCKET", "")
OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "")  # 例 https://oss-cn-wulanchabu-internal.aliyuncs.com
OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "")
OSS_OBJECT_PREFIX = os.environ.get("OSS_OBJECT_PREFIX", "cutweave/clone-tmp/")


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
