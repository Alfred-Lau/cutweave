"""FastAPI 应用与路由（P0：同步渲染，无队列）。"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional, Union

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, RootModel, ValidationError

from . import store
from .compiler import CompileError, _download, build_command, prepare_materials, run_ffmpeg
from . import voiceclone
from .config import (DATA_DIR, DEFAULT_FONT, DASHSCOPE_API_KEY,
                     RENDERS_DIR, VOLC_TTS_APPID, VOLC_TTS_TOKEN, ensure_dirs)
from .models import (Draft, KIND_TRACK_TYPE, Material, Segment, TextStyle, Transform,
                     new_id, RESOURCE_KIND)
from .probe import ProbeError, probe
from .store import DraftNotFound
from .tts import TTSError, synthesize

app = FastAPI(title="CutWeave Engine", version="0.1.0",
              description="CutWeave P0 引擎：草稿 CRUD + FFmpeg 云渲染 + TTS")

RENDER_JOBS: dict[str, dict] = {}  # P0 内存态；P1 迁移到任务总线


# ---------------- 请求模型 ----------------
class CreateDraftReq(BaseModel):
    name: str = "未命名草稿"
    width: int = Field(1080, gt=0)
    height: int = Field(1920, gt=0)
    fps: int = Field(30, ge=1, le=60)


class AddMediaReq(BaseModel):
    """video/image/audio 通用添加参数。"""
    url: str
    start: float = Field(0.0, ge=0)
    end: Optional[float] = None      # 缺省 = start + duration(或素材时长)
    duration: Optional[float] = None
    source_in: float = Field(0.0, ge=0)
    level: int = 0                   # video/image：0=主轨铺满，>0 画中画
    x: int = 0
    y: int = 0
    scale: float = Field(1.0, gt=0)
    alpha: float = Field(1.0, gt=0, le=1)
    volume: float = Field(1.0, ge=0, le=2)


class AddTextReq(BaseModel):
    text: str
    start: float = Field(0.0, ge=0)
    end: float
    font_size: int = Field(64, gt=0)
    font_color: str = "white"
    align: Literal["left", "center", "right"] = "center"
    border_color: Optional[str] = None
    border_width: int = Field(0, ge=0)
    x: int = 0
    y: int = 0  # 0 = 底部 120px 处（P0 约定）


class PatchSegReq(BaseModel):
    start: Optional[float] = None
    end: Optional[float] = None
    source_in: Optional[float] = None
    x: Optional[int] = None
    y: Optional[int] = None
    scale: Optional[float] = None
    alpha: Optional[float] = None
    volume: Optional[float] = None
    text: Optional[str] = None
    font_size: Optional[int] = None
    font_color: Optional[str] = None
    align: Optional[Literal["left", "center", "right"]] = None


class TTSReq(BaseModel):
    text: str
    voice: Optional[str] = None
    engine: Literal["edge_tts", "mac_say", "cosyvoice", "volcengine"] = "edge_tts"
    model: Optional[str] = None  # cosyvoice 模型名；其他引擎忽略


class RenderReq(BaseModel):
    draft_id: str
    crf: int = Field(20, ge=14, le=32)
    preset: Literal["ultrafast", "veryfast", "fast", "medium"] = "veryfast"


# ---------------- 工具 ----------------
def _load(draft_id: str) -> Draft:
    try:
        return store.load_draft(draft_id)
    except DraftNotFound:
        raise HTTPException(404, f"草稿不存在: {draft_id}")


def _probe_material(url: str) -> float:
    """探测素材时长；http 先走缓存下载。异常转 400。"""
    try:
        p = _download(url) if url.startswith(("http://", "https://")) else Path(url)
        return probe(p)["duration"]
    except (ProbeError, CompileError, OSError) as e:
        raise HTTPException(400, f"素材不可用: {e}")


def _add_media(draft: Draft, kind: str, req: AddMediaReq) -> dict:
    end = req.end if req.end is not None else (
        req.start + req.duration if req.duration is not None else None)
    if kind != "image" and end is None:
        end = req.start + _probe_material(req.url)
    if end is None:
        raise HTTPException(400, "image 必须提供 end 或 duration")
    m = Material(kind=kind, url=req.url)
    draft.materials.append(m)
    seg = Segment(
        material_id=m.material_id, start=req.start, end=end, source_in=req.source_in,
        transform=Transform(x=req.x, y=req.y, scale=req.scale),
        alpha=req.alpha, volume=req.volume,
    )
    draft.ensure_track(KIND_TRACK_TYPE[kind], level=req.level).segments.append(seg)
    return {"material_id": m.material_id, "segment_id": seg.segment_id,
            "start": seg.start, "end": seg.end}


def _add_text(draft: Draft, req: AddTextReq) -> dict:
    m = Material(kind="text", text=req.text,
                 style=TextStyle(font_size=req.font_size, font_color=req.font_color,
                                 align=req.align, border_color=req.border_color,
                                 border_width=req.border_width))
    draft.materials.append(m)
    seg = Segment(material_id=m.material_id, start=req.start, end=req.end,
                  transform=Transform(x=req.x, y=req.y))
    draft.ensure_track("text").segments.append(seg)
    return {"material_id": m.material_id, "segment_id": seg.segment_id,
            "start": seg.start, "end": seg.end}


def _patch_segment(draft: Draft, kind: str, segment_id: str, req: PatchSegReq) -> Segment:
    try:
        _, seg = draft.find_segment(segment_id)
    except KeyError:
        raise HTTPException(404, f"片段不存在: {segment_id}")
    m = draft.material(seg.material_id)
    if req.start is not None: seg.start = req.start
    if req.end is not None: seg.end = req.end
    if req.source_in is not None: seg.source_in = req.source_in
    if req.x is not None: seg.transform.x = req.x
    if req.y is not None: seg.transform.y = req.y
    if req.scale is not None: seg.transform.scale = req.scale
    if req.alpha is not None: seg.alpha = req.alpha
    if req.volume is not None: seg.volume = req.volume
    if req.text is not None and m.kind == "text": m.text = req.text
    if req.font_size is not None and m.kind == "text": m.style.font_size = req.font_size
    if req.font_color is not None and m.kind == "text": m.style.font_color = req.font_color
    if req.align is not None and m.kind == "text": m.style.align = req.align
    if seg.end <= seg.start:
        raise HTTPException(400, "patch 后 end 必须大于 start")
    return seg


# ---------------- 路由 ----------------
@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "font_ready": DEFAULT_FONT is not None,
            "font": DEFAULT_FONT or "未找到中文字体",
            "cosyvoice_ready": bool(DASHSCOPE_API_KEY),
            "volcengine_ready": bool(VOLC_TTS_APPID and VOLC_TTS_TOKEN)}


@app.post("/api/v1/drafts")
async def create_draft(req: CreateDraftReq):
    d = Draft(name=req.name)
    d.canvas.width, d.canvas.height, d.canvas.fps = req.width, req.height, req.fps
    store.save_draft(d)
    return d.summary()


@app.get("/api/v1/drafts")
async def list_drafts():
    return store.list_drafts()


@app.get("/api/v1/drafts/{draft_id}")
async def get_draft(draft_id: str):
    d = _load(draft_id)
    return {**d.model_dump(), "duration": round(d.duration(), 3)}


@app.delete("/api/v1/drafts/{draft_id}")
async def delete_draft(draft_id: str):
    try:
        store.delete_draft(draft_id)
    except DraftNotFound:
        raise HTTPException(404, f"草稿不存在: {draft_id}")
    return {"deleted": draft_id}


class MediaBatchBody(RootModel[Union[AddMediaReq, AddTextReq,
                                     list[Union[AddMediaReq, AddTextReq]]]]):
    """单个素材/文字对象，或其数组（批量）。"""


@app.post("/api/v1/drafts/{draft_id}/{resource}")
async def add_resource(draft_id: str, resource: str, body: MediaBatchBody) -> dict:
    """添加素材/文字；body 支持单个对象或数组（批量）。"""
    if resource not in RESOURCE_KIND:
        raise HTTPException(404, f"未知资源类型: {resource}")
    parsed = body.root
    draft = _load(draft_id)
    items: list = parsed if isinstance(parsed, list) else [parsed]
    out = {"items": []}
    for it in items:
        if RESOURCE_KIND[resource] == "text":
            if not isinstance(it, AddTextReq):
                raise HTTPException(400, "texts 只接受文字参数")
            out["items"].append(_add_text(draft, it))
        else:
            if not isinstance(it, AddMediaReq):
                raise HTTPException(400, f"{resource} 只接受素材参数")
            out["items"].append(_add_media(draft, RESOURCE_KIND[resource], it))
    draft.touch()
    store.save_draft(draft)
    return out


@app.patch("/api/v1/drafts/{draft_id}/{resource}/{segment_id}")
async def patch_resource(draft_id: str, resource: str, segment_id: str, req: PatchSegReq):
    if resource not in RESOURCE_KIND:
        raise HTTPException(404, f"未知资源类型: {resource}")
    draft = _load(draft_id)
    seg = _patch_segment(draft, RESOURCE_KIND[resource], segment_id, req)
    draft.touch()
    store.save_draft(draft)
    return {"segment_id": seg.segment_id, "start": seg.start, "end": seg.end}


@app.delete("/api/v1/drafts/{draft_id}/{resource}/{segment_id}")
async def remove_resource(draft_id: str, resource: str, segment_id: str):
    if resource not in RESOURCE_KIND:
        raise HTTPException(404, f"未知资源类型: {resource}")
    draft = _load(draft_id)
    try:
        track, seg = draft.find_segment(segment_id)
    except KeyError:
        raise HTTPException(404, f"片段不存在: {segment_id}")
    track.segments.remove(seg)
    draft.touch()
    store.save_draft(draft)
    return {"removed": segment_id}


@app.get("/api/v1/media/probe")
async def media_probe(url: str):
    try:
        return probe(url)
    except (ProbeError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/v1/ai/tts")
async def ai_tts(req: TTSReq):
    try:
        result = await synthesize(req.text, req.voice, req.engine, req.model)
    except TTSError as e:
        raise HTTPException(502, str(e))
    result["file_url"] = "/files/" + str(Path(result["file_path"]).relative_to(DATA_DIR))
    return result


@app.post("/api/v1/render/tasks")
async def create_render_task(req: RenderReq):
    """P0 同步渲染：请求阻塞至渲染完成（小样场景）。P1 迁移到任务总线异步。"""
    draft = _load(req.draft_id)
    task_id = new_id("rdr")
    job = {"task_id": task_id, "draft_id": draft.draft_id,
           "status": "running", "created_at": time.time()}
    RENDER_JOBS[task_id] = job
    out = RENDERS_DIR / f"{task_id}.mp4"
    try:
        local = prepare_materials(draft)
        draft.touch()
        store.save_draft(draft)
        has_text = any(m.kind == "text" for m in draft.materials)
        if has_text and not DEFAULT_FONT:
            raise CompileError("未找到中文字体，无法渲染文字")
        cmd = build_command(draft, out, local, DEFAULT_FONT, crf=req.crf, preset=req.preset)
        proc = await anyio.to_thread.run_sync(run_ffmpeg, cmd, 1800)
        if proc.returncode != 0:
            raise CompileError(f"ffmpeg 失败: {proc.stderr.strip()[-800:]}")
        info = probe(out)
        job.update(status="succeeded", file_url=f"/files/renders/{task_id}.mp4",
                   duration=round(info["duration"], 3),
                   width=info["width"], height=info["height"])
    except (CompileError, ProbeError) as e:
        job.update(status="failed", error=str(e)[:1000])
    except Exception as e:
        job.update(status="failed", error=f"内部错误: {e}"[:1000])
    return job


@app.get("/api/v1/render/tasks/{task_id}")
async def get_render_task(task_id: str):
    job = RENDER_JOBS.get(task_id)
    if not job:
        raise HTTPException(404, f"渲染任务不存在: {task_id}")
    return job




# ---------------- 声音克隆（CosyVoice zero-shot 复刻） ----------------
class CloneReq(BaseModel):
    name: str = ""
    audio_base64: str
    model: Optional[str] = None


@app.post("/api/v1/ai/voice/clone")
async def ai_voice_clone(req: CloneReq):
    """用参考录音克隆音色并登记，供 TTS 的 voice 参数使用。"""
    try:
        return voiceclone.clone_voice(req.name, req.audio_base64, req.model)
    except voiceclone.VoiceCloneError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"声音克隆失败: {e}"[:500])


@app.get("/api/v1/ai/voices")
async def ai_voices():
    return {"voices": voiceclone.list_voices()}


@app.delete("/api/v1/ai/voices/{voice_id}")
async def ai_voice_delete(voice_id: str):
    try:
        return voiceclone.delete_voice(voice_id)
    except Exception as e:
        raise HTTPException(502, f"删除失败: {e}"[:500])


ensure_dirs()
app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")

# 图形化控制台（同端口同源，无 CORS 问题）
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
UI_DIR.mkdir(exist_ok=True)
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
