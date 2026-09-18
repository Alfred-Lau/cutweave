"""TTS 适配层：统一接口 + 多引擎降级。

- edge_tts：默认引擎（免费、无需 key），失败或未安装自动降级
- mac_say：macOS 系统合成（离线兜底），输出 aiff 后转码 m4a

返回统一结构 {file_path, engine, duration}。
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import anyio

from .config import FFMPEG, TTS_DIR, ensure_dirs
from .probe import safe_duration


class TTSError(Exception):
    pass


_MAC_VOICE_CACHE: str | None | bool = False  # False=未探测


def detect_mac_voice() -> str | None:
    """探测 macOS 中文语音（zh_CN 优先，其次 zh_TW）。"""
    global _MAC_VOICE_CACHE
    if _MAC_VOICE_CACHE is not False:
        return _MAC_VOICE_CACHE
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and "zh_CN" in parts:
                _MAC_VOICE_CACHE = parts[0]
                return _MAC_VOICE_CACHE
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1].startswith("zh_"):
                _MAC_VOICE_CACHE = parts[0]
                return _MAC_VOICE_CACHE
    except Exception:
        pass
    _MAC_VOICE_CACHE = None
    return _MAC_VOICE_CACHE


async def _edge_tts(text: str, voice: str | None, out_base: Path) -> dict:
    import edge_tts  # 延迟导入：未安装时走降级

    mp3 = out_base.with_suffix(".mp3")
    await edge_tts.Communicate(text, voice or "zh-CN-XiaoxiaoNeural").save(str(mp3))
    if not mp3.exists() or mp3.stat().st_size == 0:
        raise TTSError("edge_tts 输出为空")
    return {"file_path": str(mp3), "engine": "edge_tts", "duration": safe_duration(str(mp3))}


def _mac_say_sync(text: str, voice: str | None, out_base: Path) -> dict:
    aiff = out_base.with_suffix(".aiff")
    m4a = out_base.with_suffix(".m4a")
    v = voice or detect_mac_voice()
    cmd = ["say"] + (["-v", v] if v else []) + ["-o", str(aiff), text]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not aiff.exists():
        raise TTSError(f"say 合成失败: {r.stderr.strip()[:200]}")
    conv = subprocess.run(
        [FFMPEG, "-y", "-i", str(aiff), "-c:a", "aac", "-b:a", "96k", str(m4a)],
        capture_output=True, text=True, timeout=120,
    )
    aiff.unlink(missing_ok=True)
    if conv.returncode != 0 or not m4a.exists():
        raise TTSError(f"音频转码失败: {conv.stderr.strip()[:200]}")
    return {"file_path": str(m4a), "engine": "mac_say", "duration": safe_duration(str(m4a))}


async def synthesize(text: str, voice: str | None = None, engine: str = "edge_tts") -> dict:
    """合成语音。engine 指定优先引擎，失败自动降级到另一引擎。"""
    text = (text or "").strip()
    if not text:
        raise TTSError("text 不能为空")
    ensure_dirs()
    out_base = TTS_DIR / f"tts_{uuid.uuid4().hex[:12]}"

    order = ["edge_tts", "mac_say"] if engine != "mac_say" else ["mac_say", "edge_tts"]
    errors: list[str] = []
    for eng in order:
        try:
            if eng == "edge_tts":
                if not shutil.which("ffmpeg"):  # 仅为环境自检提示
                    pass
                return await _edge_tts(text, voice, out_base)
            else:
                return await anyio.to_thread.run_sync(_mac_say_sync, text, voice, out_base)
        except TTSError:
            raise
        except ImportError:
            errors.append(f"{eng}: 未安装")
        except Exception as e:  # 网络/引擎异常 → 降级
            errors.append(f"{eng}: {e}")
    raise TTSError("所有 TTS 引擎均失败：" + "；".join(errors))
