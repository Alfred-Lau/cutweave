"""时间线数据模型（P0 简化版，对标剪映草稿的核心结构）。

轨道与层级约定：
- Track.type: video | audio | text
- Track.level: 视频轨叠放层级，0 = 主轨（铺满画布），数值越大越靠上层（画中画）
- Segment.start/end 均为时间线时间轴（秒）；素材内部截取起点用 Segment.source_in

P0 明确不支持（留待 P1+）：关键帧动画、转场、蒙版、特效、贴纸、滤镜。
"""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Canvas(BaseModel):
    width: int = Field(1080, gt=0)
    height: int = Field(1920, gt=0)
    fps: int = Field(30, ge=1, le=60)


class TextStyle(BaseModel):
    font_size: int = Field(64, gt=0)
    font_color: str = "white"
    align: Literal["left", "center", "right"] = "center"
    border_color: Optional[str] = None  # 描边颜色，如 black
    border_width: int = Field(0, ge=0)  # 描边宽度(px)


class Transform(BaseModel):
    x: int = 0  # 相对画布左上角（文字轨另有对齐语义）
    y: int = 0
    scale: float = Field(1.0, gt=0)


class Material(BaseModel):
    material_id: str = Field(default_factory=lambda: new_id("mat"))
    kind: Literal["video", "image", "audio", "text"]
    url: Optional[str] = None  # http(s) 链接或本地路径；text 类型为空
    text: Optional[str] = None  # 仅 kind=text
    style: TextStyle = Field(default_factory=TextStyle)
    width: Optional[int] = None  # probe 后回填
    height: Optional[int] = None
    duration: Optional[float] = None  # probe 后回填（秒）


class Segment(BaseModel):
    segment_id: str = Field(default_factory=lambda: new_id("seg"))
    material_id: str
    start: float = Field(0.0, ge=0)
    end: float
    source_in: float = Field(0.0, ge=0)  # 素材内部起点（秒）
    transform: Transform = Field(default_factory=Transform)
    alpha: float = Field(1.0, gt=0, le=1)
    volume: float = Field(1.0, ge=0, le=2)

    @model_validator(mode="after")
    def _check_range(self):
        if self.end <= self.start:
            raise ValueError(f"end({self.end}) 必须大于 start({self.start})")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class Track(BaseModel):
    track_id: str = Field(default_factory=lambda: new_id("trk"))
    type: Literal["video", "audio", "text"]
    level: int = 0
    name: str = ""
    segments: list["Segment"] = Field(default_factory=list)


class Draft(BaseModel):
    draft_id: str = Field(default_factory=lambda: new_id("dfd"))
    name: str = "未命名草稿"
    canvas: Canvas = Field(default_factory=Canvas)
    materials: list[Material] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    # ---------- 便捷方法 ----------
    def material(self, material_id: str) -> Material:
        for m in self.materials:
            if m.material_id == material_id:
                return m
        raise KeyError(f"素材不存在: {material_id}")

    def find_segment(self, segment_id: str) -> tuple[Track, Segment]:
        for t in self.tracks:
            for s in t.segments:
                if s.segment_id == segment_id:
                    return t, s
        raise KeyError(f"片段不存在: {segment_id}")

    def ensure_track(self, type_: str, level: int = 0, name: str = "") -> Track:
        for t in self.tracks:
            if t.type == type_ and t.level == level:
                return t
        t = Track(type=type_, level=level, name=name or f"{type_}_{level}")
        self.tracks.append(t)
        return t

    def duration(self) -> float:
        ends = [s.end for t in self.tracks for s in t.segments]
        return max(ends) if ends else 0.0

    def touch(self) -> None:
        self.updated_at = time.time()

    def summary(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "name": self.name,
            "canvas": self.canvas.model_dump(),
            "duration": round(self.duration(), 3),
            "material_count": len(self.materials),
            "segment_count": sum(len(t.segments) for t in self.tracks),
            "created_at": self.created_at,
        }


# P0 允许的资源路由名 → 素材类型
RESOURCE_KIND: dict[str, str] = {
    "texts": "text",
    "images": "image",
    "videos": "video",
    "audios": "audio",
}
KIND_TRACK_TYPE: dict[str, str] = {
    "video": "video",
    "image": "video",  # 图片也走视频轨（overlay）
    "audio": "audio",
    "text": "text",
}
