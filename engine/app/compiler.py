"""FFmpeg 命令编译器：Draft JSON → ffmpeg 命令。

P0 渲染模型：
- 基底：黑色画布源（color=black），时长 = 草稿总时长
- 视觉轨（video/image 段）：全部作为 overlay 叠加
  - level 0：铺满画布（scale+crop）
  - level>0：画中画，按 transform.scale 缩放、transform.x/y 定位、alpha 半透明
- 文字轨：Pillow 预渲染透明 PNG → overlay（不依赖 ffmpeg drawtext/libfreetype）
- 音频轨：aformat → adelay → volume → amix（normalize=0 保留各自音量）
- 视频素材自带音轨 P0 不参与混音（只渲染 audio 轨）

TODO(P1+)：关键帧动画（sendcmd/zoompan）、转场（xfade）、蒙版、特效、贴纸、滤镜、
          音频段淡入淡出、字幕轨 ASS 渲染。
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import CACHE_DIR, FFMPEG, ensure_dirs
from .models import Draft, Material, Segment
from .probe import probe
from .textrender import render_text_png


class CompileError(Exception):
    pass


def _is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _download(url: str) -> Path:
    """下载远程素材到本地缓存（同 URL 复用），返回本地路径。"""
    ensure_dirs()
    ext = Path(urlparse(url).path).suffix or ".bin"
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
    dest = CACHE_DIR / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
    return dest


def _local_path(url: str) -> Path:
    p = Path(url)
    if not p.exists():
        raise CompileError(f"素材文件不存在: {url}")
    return p


def prepare_materials(draft: Draft) -> dict[str, str]:
    """素材落地为本地路径并 probe 回填宽高/时长；返回 material_id -> local_path。"""
    local: dict[str, str] = {}
    for m in draft.materials:
        if m.kind == "text":
            continue
        if not m.url:
            raise CompileError(f"素材 {m.material_id}({m.kind}) 缺少 url")
        p = _download(m.url) if _is_http(m.url) else _local_path(m.url)
        info = probe(p)
        if info["duration"] and not m.duration:
            m.duration = info["duration"]
        m.width, m.height = info["width"], info["height"]
        local[m.material_id] = str(p)
    return local


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def build_command(draft: Draft, out_path: str | Path, local: dict[str, str],
                  fontfile: str | None, crf: int = 20, preset: str = "veryfast") -> list[str]:
    """生成 ffmpeg 命令。local 为 prepare_materials 的返回值。"""
    W, H, fps = draft.canvas.width, draft.canvas.height, draft.canvas.fps
    total = draft.duration()
    if total <= 0:
        raise CompileError("草稿没有任何片段，无法渲染")

    inputs: list[list[str]] = [
        ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={_fmt(total)}"]
    ]
    parts: list[str] = []
    last_v = "0:v"

    # ---------- 视觉层（video/image），主轨在前、画中画按 level 升序 ----------
    visual: list[tuple[int, Segment, Material]] = []  # (level, seg, mat)
    for t in sorted((t for t in draft.tracks if t.type == "video"), key=lambda t: t.level):
        for s in t.segments:
            m = draft.material(s.material_id)
            if m.kind not in ("video", "image"):
                continue
            visual.append((t.level, s, m))
    visual.sort(key=lambda x: (x[0] != 0, x[0], x[1].start))

    for level, seg, mat in visual:
        path = local[mat.material_id]
        dur = seg.duration
        if mat.kind == "video":
            inputs.append(["-ss", _fmt(seg.source_in), "-t", _fmt(dur), "-i", path])
            chain = f"[{len(inputs)-1}:v]"
        else:  # image
            inputs.append(["-loop", "1", "-t", _fmt(dur), "-i", path])
            chain = f"[{len(inputs)-1}:v]"

        if level == 0:  # 主轨铺满
            parts.append(f"{chain}scale={W}:{H}:force_original_aspect_ratio=increase,"
                         f"crop={W}:{H},setsar=1[lv{level}_{seg.segment_id}]")
        else:  # 画中画
            if not (mat.width and mat.height):
                raise CompileError(f"素材 {mat.material_id} 缺少分辨率信息")
            tw = int(mat.width * seg.transform.scale) // 2 * 2
            chain_expr = f"{chain}scale={tw}:-2"
            if seg.alpha < 1.0:
                chain_expr += f",colorchannelmixer=aa={seg.alpha:.3f}"
            parts.append(f"{chain_expr}[lv{level}_{seg.segment_id}]")

        tag = f"lv{level}_{seg.segment_id}"
        x, y = seg.transform.x, seg.transform.y
        if level == 0:
            x, y = 0, 0
        parts.append(
            f"[{last_v}][{tag}]overlay=x={x}:y={y}:eof_action=pass:"
            f"enable='between(t,{_fmt(seg.start)},{_fmt(seg.end)})'[v_{seg.segment_id}]"
        )
        last_v = f"v_{seg.segment_id}"

    # ---------- 文字层（Pillow 预渲染 PNG → overlay） ----------
    for t in draft.tracks:
        if t.type != "text":
            continue
        for s in t.segments:
            m = draft.material(s.material_id)
            if m.kind != "text" or not m.text:
                continue
            if not fontfile:
                raise CompileError("未找到可用中文字体，无法渲染文字（检查 config.detect_font）")
            png, tw, th = render_text_png(m.text, m.style, fontfile)
            inputs.append(["-loop", "1", "-t", _fmt(s.duration), "-i", str(png)])
            x = s.transform.x
            if m.style.align == "center":
                x_expr = f"({W}-{tw})/2" + (f"+{x}" if x else "")
            elif m.style.align == "right":
                x_expr = f"{W}-{tw}-{abs(x)}"
            else:
                x_expr = str(x)
            # P0 约定：transform.y=0 → 底部 120px 处；否则为绝对 y
            y_expr = f"{H}-{th}-120" if s.transform.y == 0 else str(s.transform.y)
            parts.append(
                f"[{last_v}][{len(inputs)-1}:v]overlay=x={x_expr}:y={y_expr}:"
                f"enable='between(t,{_fmt(s.start)},{_fmt(s.end)})'[tx_{s.segment_id}]"
            )
            last_v = f"tx_{s.segment_id}"

    parts.append(f"[{last_v}]format=yuv420p[vout]")

    # ---------- 音频层 ----------
    a_idx = 0
    amix_in: list[str] = []
    for t in draft.tracks:
        if t.type != "audio":
            continue
        for s in t.segments:
            m = draft.material(s.material_id)
            if m.kind != "audio" or m.material_id not in local:
                continue
            inputs.append(["-ss", _fmt(s.source_in), "-t", _fmt(s.duration), "-i", local[m.material_id]])
            ms = int(s.start * 1000)
            parts.append(
                f"[{len(inputs)-1}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                f"adelay={ms}|{ms},volume={s.volume:.3f}[a{a_idx}]"
            )
            amix_in.append(f"[a{a_idx}]")
            a_idx += 1

    map_audio: list[str] = []
    if a_idx == 1:
        parts.append(f"{amix_in[0]}anull[aout]")
        map_audio = ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    elif a_idx > 1:
        parts.append(f"{''.join(amix_in)}amix=inputs={a_idx}:normalize=0[aout]")
        map_audio = ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    else:
        map_audio = ["-an"]

    cmd = [FFMPEG, "-y"]
    for inp in inputs:
        cmd += inp
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", *map_audio,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-t", _fmt(total), "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    return cmd


def run_ffmpeg(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    ensure_dirs()
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
