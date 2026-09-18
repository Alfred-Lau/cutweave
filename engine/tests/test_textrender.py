"""文字渲染器测试（PIL 预渲染文字图层）。"""
from pathlib import Path

import pytest
from PIL import Image

from app.config import DEFAULT_FONT
from app.models import TextStyle
from app.textrender import render_text_png

pytestmark = pytest.mark.skipif(DEFAULT_FONT is None, reason="本机无可用中文字体")


def test_render_and_cache():
    st = TextStyle(font_size=48, font_color="white",
                   border_color="black", border_width=3)
    p1, w1, h1 = render_text_png("你好，流光剪影", st, DEFAULT_FONT)
    assert Path(p1).exists() and w1 > 0 and h1 > 0
    with Image.open(p1) as im:
        assert im.mode == "RGBA" and im.size == (w1, h1)
    # 同 内容+样式+字体 命中缓存
    p2, w2, h2 = render_text_png("你好，流光剪影", st, DEFAULT_FONT)
    assert p1 == p2 and (w2, h2) == (w1, h1)


def test_alpha_color_and_multiline():
    st = TextStyle(font_size=32, font_color="yellow@0.5")
    p, w, h = render_text_png("半透明文字\n第二行", st, DEFAULT_FONT)
    assert w > 0 and h > 0


def test_empty_text_rejected():
    with pytest.raises(ValueError):
        render_text_png("   ", TextStyle(), DEFAULT_FONT)
