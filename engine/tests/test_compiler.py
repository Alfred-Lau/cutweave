"""FFmpeg 编译器命令生成测试（不执行 ffmpeg，只断言命令结构）。"""
import pytest

from app.compiler import CompileError, build_command
from app.config import DEFAULT_FONT
from app.models import Canvas, Draft, Material, Segment, TextStyle, Transform

# 含文字层的命令生成需要真实可用字体（PIL 预渲染）
pytestmark = pytest.mark.skipif(DEFAULT_FONT is None, reason="本机无可用中文字体")


def make_draft() -> Draft:
    d = Draft(name="t", canvas=Canvas(width=720, height=1280, fps=30))
    bg = Material(kind="video", url="/tmp/bg.mp4", material_id="m_bg", width=1920, height=1080)
    pip = Material(kind="image", url="/tmp/pip.png", material_id="m_pip", width=400, height=400)
    txt = Material(kind="text", text="你好: 100% 'ok'", material_id="m_txt",
                   style=TextStyle(font_size=64, font_color="white", align="center",
                                   border_color="black", border_width=3))
    aud = Material(kind="audio", url="/tmp/a.m4a", material_id="m_aud", duration=5)
    d.materials += [bg, pip, txt, aud]
    tv = d.ensure_track("video", level=0)
    tv.segments.append(Segment(material_id="m_bg", start=0, end=10))
    tp = d.ensure_track("video", level=2)
    tp.segments.append(Segment(material_id="m_pip", start=1, end=4,
                               transform=Transform(x=100, y=80, scale=0.5), alpha=0.8))
    d.ensure_track("text").segments.append(Segment(material_id="m_txt", start=0, end=5))
    d.ensure_track("audio").segments.append(Segment(material_id="m_aud", start=0.5, end=5.5, volume=0.6))
    return d


LOCAL = {"m_bg": "/tmp/bg.mp4", "m_pip": "/tmp/pip.png", "m_aud": "/tmp/a.m4a"}


def test_build_command_structure():
    d = make_draft()
    cmd = build_command(d, "/tmp/out.mp4", LOCAL, DEFAULT_FONT)
    s = " ".join(cmd)
    # 基底黑场 + 时长
    assert "color=c=black:s=720x1280:r=30:d=10" in s
    # 主轨铺满
    assert "force_original_aspect_ratio=increase" in s and "crop=720:1280" in s
    # 画中画缩放与半透明
    assert "scale=200:-2" in s and "colorchannelmixer=aa=0.800" in s
    assert "overlay=x=100:y=80" in s
    # 文字层：PIL 预渲染 PNG → overlay（无 drawtext/libfreetype 依赖）
    assert "drawtext" not in s
    assert "overlay=x=(720-" in s          # center: (W-tw)/2
    assert "y=1280-" in s and s.count("-120:") >= 1  # 底部 120px 锚点（H=1280）
    assert s.count("overlay=") == 3        # 背景主轨 + 画中画 + 文字
    # 音频延迟与混音
    assert "adelay=500|500" in s and "volume=0.600" in s
    assert "amix=inputs=1" not in s  # 单音频直接 anull，不走 amix
    assert "anull[aout]" in s
    # 输出参数
    assert "-map" in s and "[vout]" in s and "libx264" in s and "-pix_fmt" in " ".join(cmd)
    assert s.strip().endswith("/tmp/out.mp4")


def test_build_command_amix_for_multi_audio():
    d = make_draft()
    m2 = Material(kind="audio", url="/tmp/b.m4a", material_id="m_aud2", duration=3)
    d.materials.append(m2)
    d.ensure_track("audio").segments.append(
        Segment(material_id="m_aud2", start=0, end=2, volume=1.0))
    LOCAL2 = {**LOCAL, "m_aud2": "/tmp/b.m4a"}
    s = " ".join(build_command(d, "/tmp/out.mp4", LOCAL2, DEFAULT_FONT))
    assert "amix=inputs=2:normalize=0" in s


def test_empty_draft_rejected():
    d = Draft()
    with pytest.raises(CompileError):
        build_command(d, "/tmp/x.mp4", {}, None)
