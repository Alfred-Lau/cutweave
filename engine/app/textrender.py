"""文字渲染器：文字段 → 透明 PNG（供 ffmpeg overlay）。

选型说明：不依赖 ffmpeg 的 drawtext/libfreetype（云端 ffmpeg 构建差异大），
用 Pillow 预渲染文字图层，同时为 P2 的花字/文字模板体系打基础。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .config import ensure_dirs
from .models import TextStyle

TEXT_CACHE_DIR_NAME = "textcache"


def _cache_dir() -> Path:
    ensure_dirs()
    from .config import DATA_DIR
    d = DATA_DIR / TEXT_CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _to_rgba(color: str):
    """兼容 ffmpeg 风格 'color@alpha'（如 white@0.8）与命名色/hex。"""
    alpha = 255
    base = color
    if "@" in color:
        base, a = color.split("@", 1)
        try:
            alpha = int(float(a) * 255)
        except ValueError:
            alpha = 255
    r, g, b = ImageColor.getrgb(base)[:3]
    return (r, g, b, alpha)


def render_text_png(text: str, style: TextStyle, fontfile: str) -> tuple[Path, int, int]:
    """渲染文字为 RGBA PNG；返回 (路径, 宽, 高)。同 内容+样式+字体 命中缓存。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("文字内容为空")
    key = hashlib.sha1(f"{text}|{style.model_dump_json()}|{fontfile}".encode()).hexdigest()[:16]
    png = _cache_dir() / f"txt_{key}.png"
    if png.exists():
        with Image.open(png) as im:
            return png, im.width, im.height

    font = ImageFont.truetype(fontfile, style.font_size)
    stroke = style.border_width
    pad = stroke + 4
    fill = _to_rgba(style.font_color)
    stroke_fill = _to_rgba(style.border_color) if (stroke > 0 and style.border_color) else None

    lines = text.split("\n")
    probe_img = Image.new("RGBA", (8, 8))
    probe = ImageDraw.Draw(probe_img)
    widths: list[int] = []
    heights: list[int] = []
    for ln in lines:
        box = probe.textbbox((0, 0), ln or " ", font=font, stroke_width=stroke)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    line_h = max(heights) if heights else style.font_size
    w = max(widths) + pad * 2
    h = line_h * len(lines) + pad * 2
    w, h = max(w, 1), max(h, 1)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        kwargs = dict(font=font, fill=fill)
        if stroke_fill:
            kwargs.update(stroke_width=stroke, stroke_fill=stroke_fill)
        d.text((pad, pad + i * line_h), ln, **kwargs)
    img.save(png)
    return png, w, h
