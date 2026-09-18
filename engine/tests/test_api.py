"""API 集成测试：CRUD + probe + 真实渲染冒烟（ffmpeg lavfi 素材，秒级完成）。"""
import subprocess

import httpx
import pytest

from app.config import FFMPEG, DATA_DIR

from fastapi.testclient import TestClient  # noqa: F401  (Py3.14+ httpx>=0.27 兼容)
import app.main as main_mod

client = TestClient(main_mod.app)


@pytest.fixture(scope="module")
def bg_video(tmp_path_factory):
    """lavfi 生成 3s 测试背景视频（含音轨，用于确认 P0 忽略视频音轨的设计）。"""
    p = tmp_path_factory.mktemp("assets") / "bg.mp4"
    subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x568:duration=3:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(p),
    ], check=True, capture_output=True)
    return str(p)


def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_draft_crud_cycle(bg_video):
    r = client.post("/api/v1/drafts", json={"name": "api测试", "width": 320, "height": 568, "fps": 15})
    assert r.status_code == 200
    draft_id = r.json()["draft_id"]

    # 加背景视频
    r = client.post(f"/api/v1/drafts/{draft_id}/videos",
                    json={"url": bg_video, "start": 0, "end": 3})
    assert r.status_code == 200
    seg_bg = r.json()["items"][0]["segment_id"]

    # 加文字 + 批量（数组）加第二条文字
    r = client.post(f"/api/v1/drafts/{draft_id}/texts", json={
        "text": "你好流光", "start": 0, "end": 2, "font_size": 40})
    assert r.status_code == 200
    seg_txt = r.json()["items"][0]["segment_id"]

    # patch 文字时间
    r = client.patch(f"/api/v1/drafts/{draft_id}/texts/{seg_txt}",
                     json={"start": 0.5, "end": 2.5, "font_color": "yellow"})
    assert r.status_code == 200 and r.json()["end"] == pytest.approx(2.5)

    # 非法 patch：end <= start
    r = client.patch(f"/api/v1/drafts/{draft_id}/texts/{seg_txt}", json={"start": 9, "end": 1})
    assert r.status_code == 400

    # 查询 script
    r = client.get(f"/api/v1/drafts/{draft_id}")
    assert r.status_code == 200
    script = r.json()
    assert script["duration"] == pytest.approx(3)
    assert len(script["materials"]) == 2

    # 删除片段 → 再删除草稿
    assert client.delete(f"/api/v1/drafts/{draft_id}/videos/{seg_bg}").status_code == 200
    assert client.delete(f"/api/v1/drafts/{draft_id}").status_code == 200
    assert client.get(f"/api/v1/drafts/{draft_id}").status_code == 404


def test_probe_endpoint(bg_video):
    r = client.get("/api/v1/media/probe", params={"url": bg_video})
    assert r.status_code == 200
    data = r.json()
    assert data["duration"] == pytest.approx(3, abs=0.3)
    assert data["width"] == 320 and data["has_audio"] is True


def test_render_smoke(bg_video):
    """渲染冒烟：背景视频 + 文字 + 系统音轨（用视频自身音轨不可行 → 音频轨用 sine 文件）。"""
    sine = bg_video.replace("bg.mp4", "tone.m4a")
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:a", "aac", sine], check=True, capture_output=True)

    draft_id = client.post("/api/v1/drafts", json={
        "name": "render冒烟", "width": 320, "height": 568, "fps": 15}).json()["draft_id"]
    client.post(f"/api/v1/drafts/{draft_id}/videos", json={"url": bg_video, "start": 0, "end": 3})
    client.post(f"/api/v1/drafts/{draft_id}/texts", json={
        "text": "P0 渲染", "start": 0, "end": 3, "font_size": 36})
    client.post(f"/api/v1/drafts/{draft_id}/audios", json={"url": sine, "start": 0, "end": 3})

    r = client.post("/api/v1/render/tasks", json={"draft_id": draft_id, "preset": "ultrafast"})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "succeeded", job.get("error")
    assert job["duration"] == pytest.approx(3, abs=0.3)

    # 成片文件真实存在且可再次查询
    out = DATA_DIR / "renders" / f"{job['task_id']}.mp4"
    assert out.exists() and out.stat().st_size > 1000
    assert client.get(f"/api/v1/render/tasks/{job['task_id']}").json()["status"] == "succeeded"

    # 静态文件可下载
    dl = client.get(job["file_url"])
    assert dl.status_code == 200 and len(dl.content) > 1000
