"""ffprobe 封装：探测媒体时长 / 分辨率 / 是否含音轨。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import FFPROBE


class ProbeError(Exception):
    pass


def probe(url_or_path: str) -> dict:
    """返回 {duration, width, height, has_audio, codec}；探测失败抛 ProbeError。"""
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        "-of", "json",
        str(url_or_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"probe 超时: {url_or_path}") from e
    if r.returncode != 0:
        raise ProbeError(f"probe 失败: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout or "{}")

    duration = 0.0
    fmt = data.get("format", {})
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    width = height = None
    has_audio = False
    codec = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and width is None:
            width, height = s.get("width"), s.get("height")
            codec = s.get("codec_name")
        elif s.get("codec_type") == "audio":
            has_audio = True
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "codec": codec,
    }


def safe_duration(url_or_path: str, default: float = 0.0) -> float:
    try:
        return probe(url_or_path)["duration"]
    except (ProbeError, OSError):
        return default
