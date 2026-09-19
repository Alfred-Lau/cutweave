"""TTS 适配层：统一接口 + 多引擎 + 长文本分段拼接。

引擎：
- edge_tts：默认（免费、无需 key）
- cosyvoice：阿里百炼 CosyVoice（支持克隆音色，需 DASHSCOPE_API_KEY）
- mac_say：macOS 系统合成（离线兜底），输出 aiff 后转码 m4a

长文本（>400 字）自动按句切分逐段合成，再 ffmpeg concat 成单文件。
显式指定的 engine 失败时直接报错，不静默降级（避免克隆请求悄悄变成免费音色）。

返回统一结构 {file_path, engine, duration[, segments]}。
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import anyio
import httpx

from . import config
from .config import COSYVOICE_MODEL, DASHSCOPE_API_KEY, FFMPEG, TTS_DIR, ensure_dirs
from .probe import safe_duration

# 单段合成字符上限：低于平台限制，且短句韵律更稳定
SEGMENT_MAX_CHARS = 400


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


def split_text(text: str, max_chars: int = SEGMENT_MAX_CHARS) -> list[str]:
    """按句边界切分长文本，贪婪合并至 max_chars 以内。

    三级降级：句末标点 → 逗号/顿号 → 硬切。保证每段 ≤ max_chars 且拼接后与原文一致。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    for sent in re.split(r"(?<=[。！？!?；;\n])", text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            parts.append(sent)
            continue
        buf = ""
        for piece in re.split(r"(?<=[，,、])", sent):
            if len(buf) + len(piece) <= max_chars:
                buf += piece
                continue
            if buf.strip():
                parts.append(buf.strip())
            if len(piece) <= max_chars:
                buf = piece
            else:
                tail = (buf + piece).strip() if buf else piece
                parts.extend(tail[i:i + max_chars] for i in range(0, len(tail), max_chars))
                buf = ""
        if buf.strip():
            parts.append(buf.strip())

    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) + len(p) <= max_chars:
            merged[-1] += p
        else:
            merged.append(p)
    return [p for p in merged if p.strip()]


# ---------------- 引擎实现（单段） ----------------

async def _edge_tts(text: str, voice: str | None, target: Path) -> Path:
    import edge_tts  # 延迟导入：未安装时走降级

    await edge_tts.Communicate(text, voice or "zh-CN-XiaoxiaoNeural").save(str(target))
    if not target.exists() or target.stat().st_size == 0:
        raise TTSError("edge_tts 输出为空")
    return target


def _cosyvoice_sync(text: str, voice: str | None, model: str | None, target: Path) -> Path:
    """CosyVoice 单段合成（同步 WebSocket，线程内执行）。"""
    if not DASHSCOPE_API_KEY:
        raise TTSError("cosyvoice 需要 DASHSCOPE_API_KEY（百炼控制台创建后设置环境变量）")
    try:
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError as e:
        raise TTSError("缺少依赖 dashscope（requirements 已含，检查安装）") from e
    kwargs = {"model": model or COSYVOICE_MODEL}
    if voice:
        kwargs["voice"] = voice  # 预设音色名或克隆 voice_id
    try:
        synth = SpeechSynthesizer(**kwargs)
        audio = synth.call(text)
    except TTSError:
        raise
    except Exception as e:
        raise TTSError(f"cosyvoice 合成失败: {e}")
    if not audio:
        raise TTSError("cosyvoice 返回空音频")
    target.write_bytes(audio)
    return target


def _volc_sync(text: str, voice: str | None, model: str | None, target: Path) -> Path:
    """火山引擎大模型语音合成（HTTP 一次性合成，线程内执行）。

    voice 支持官方音色名（zh_female_* / zh_male_*）或控制台复刻的声音 ID（S_ 开头）。
    """
    appid, token = config.VOLC_TTS_APPID, config.VOLC_TTS_TOKEN
    if not appid or not token:
        raise TTSError("volcengine 需要 VOLC_TTS_APPID / VOLC_TTS_TOKEN（豆包语音控制台获取）")
    v = voice or config.VOLC_TTS_VOICE
    if not v:
        raise TTSError("volcengine 需要指定 voice（官方音色名或复刻声音 ID）")
    payload = {
        "app": {"appid": appid, "token": token, "cluster": config.VOLC_TTS_CLUSTER},
        "user": {"uid": "cutweave"},
        "audio": {"voice_type": v, "encoding": "mp3"},
        "request": {"reqid": uuid.uuid4().hex, "text": text, "operation": "query"},
    }
    try:
        r = httpx.post("https://openspeech.bytedance.com/api/v1/tts", json=payload,
                       headers={"Authorization": f"Bearer;{token}"}, timeout=120)
    except httpx.HTTPError as e:
        raise TTSError(f"volcengine 网络错误: {e}")
    try:
        data = r.json()
    except ValueError:
        raise TTSError(f"volcengine 响应异常: HTTP {r.status_code}")
    if r.status_code != 200 or data.get("code") != 3000 or not data.get("data"):
        raise TTSError(f"volcengine 合成失败: HTTP {r.status_code} "
                       f"code={data.get('code')} {str(data.get('message'))[:200]}")
    target.write_bytes(base64.b64decode(data["data"]))
    return target


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


def _concat_sync(paths: list[Path], out: Path) -> None:
    """ffmpeg concat demuxer 拼接分段音频 → aac/m4a。"""
    lst = out.parent / f"{out.stem}_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in paths))
    r = subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:a", "aac", "-b:a", "128k", str(out)],
        capture_output=True, text=True, timeout=600)
    lst.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists():
        raise TTSError(f"分段音频拼接失败: {r.stderr.strip()[-300:]}")


async def _synth_long(text: str, voice: str | None, out_base: Path,
                      kind: str, model: str | None = None) -> dict:
    """分段合成 + 拼接；单段时走各引擎原始输出。"""
    segs = split_text(text)
    if kind not in ("edge_tts", "cosyvoice", "volcengine"):
        raise TTSError(f"未知引擎: {kind}")

    def _cloud_seg(seg: str, target: Path) -> Path:
        if kind == "cosyvoice":
            return _cosyvoice_sync(seg, voice, model, target)
        return _volc_sync(seg, voice, model, target)

    if len(segs) == 1:
        if kind == "edge_tts":
            p = await _edge_tts(segs[0], voice, out_base.with_suffix(".mp3"))
        else:
            p = await anyio.to_thread.run_sync(
                _cloud_seg, segs[0], out_base.with_suffix(".mp3"))
        return {"file_path": str(p), "engine": kind, "duration": safe_duration(str(p))}

    seg_files: list[Path] = []
    for i, seg in enumerate(segs):
        p = out_base.parent / f"{out_base.name}_seg{i:03d}.mp3"
        if kind == "edge_tts":
            await _edge_tts(seg, voice, p)
        else:
            await anyio.to_thread.run_sync(_cloud_seg, seg, p)
        seg_files.append(p)
    out = out_base.with_suffix(".m4a")
    await anyio.to_thread.run_sync(_concat_sync, seg_files, out)
    for f in seg_files:
        f.unlink(missing_ok=True)
    return {"file_path": str(out), "engine": kind,
            "duration": safe_duration(str(out)), "segments": len(seg_files)}


async def synthesize(text: str, voice: str | None = None,
                     engine: str = "edge_tts", model: str | None = None) -> dict:
    """合成语音。engine 指定优先引擎；cosyvoice 失败直接报错，不降级。"""
    text = (text or "").strip()
    if not text:
        raise TTSError("text 不能为空")
    ensure_dirs()
    out_base = TTS_DIR / f"tts_{uuid.uuid4().hex[:12]}"

    if engine in ("cosyvoice", "volcengine"):
        return await _synth_long(text, voice, out_base, kind=engine, model=model)

    order = ["edge_tts", "mac_say"] if engine != "mac_say" else ["mac_say", "edge_tts"]
    errors: list[str] = []
    for eng in order:
        try:
            if eng == "edge_tts":
                if not shutil.which("ffmpeg"):  # 仅为环境自检提示
                    pass
                return await _synth_long(text, voice, out_base, kind="edge_tts")
            return await anyio.to_thread.run_sync(_mac_say_sync, text, voice, out_base)
        except TTSError:
            raise
        except ImportError:
            errors.append(f"{eng}: 未安装")
        except Exception as e:  # 网络/引擎异常 → 降级
            errors.append(f"{eng}: {e}")
    raise TTSError("所有 TTS 引擎均失败：" + "；".join(errors))
