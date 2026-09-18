"""P0 一键端到端演示：
起本地服务 → 生成测试素材 → 建草稿（背景视频 + TTS 配音 + 标题文字）→ 云渲染 → ffprobe 验证。

用法：
    cd engine && .venv/bin/python scripts/demo_e2e.py
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR, FFPROBE  # noqa: E402

BASE = "http://127.0.0.1:8390"
PORT = 8390


def api(method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if payload is None else __import__("json").dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return __import__("json").loads(r.read().decode())


def wait_health(timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/v1/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务未在时限内就绪")


def main() -> int:
    ensure = subprocess.run([FFPROBE, "-version"], capture_output=True)
    assert ensure.returncode == 0, "ffprobe 不可用"

    # 1. 起服务
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "-m", "uvicorn", "app.main:app",
         "--port", str(PORT)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_health()
        print("[1/6] 服务已启动")

        # 2. 生成测试背景视频（10s 竖屏）
        assets = DATA_DIR / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        bg = assets / "demo_bg.mp4"
        if not bg.exists():
            subprocess.run([
                __import__("app.config", fromlist=["FFMPEG"]).FFMPEG, "-y",
                "-f", "lavfi", "-i", "testsrc2=size=720x1280:duration=10:rate=30",
                "-c:v", "libx264", "-preset", "veryfast", str(bg)],
                check=True, capture_output=True)
        print(f"[2/6] 背景素材就绪: {bg.name}")

        # 3. 建草稿
        d = api("POST", "/api/v1/drafts",
                {"name": "demo_第一条自动视频", "width": 720, "height": 1280})
        draft_id = d["draft_id"]
        print(f"[3/6] 草稿已创建: {draft_id}")

        # 4. 背景视频 + TTS 配音 + 标题文字
        api("POST", f"/api/v1/drafts/{draft_id}/videos",
            {"url": str(bg), "start": 0, "end": 10, "level": 0})
        tts = api("POST", "/api/v1/ai/tts",
                  {"text": "你好，这是 CutWeave 引擎的第一条自动生成视频"})
        print(f"[4/6] TTS 完成（engine={tts['engine']}，{tts['duration']:.1f}s）")
        api("POST", f"/api/v1/drafts/{draft_id}/audios",
            {"url": tts["file_path"], "start": 0.5,
             "end": 0.5 + min(tts["duration"], 9.0)})
        api("POST", f"/api/v1/drafts/{draft_id}/texts",
            {"text": "CutWeave · P0", "start": 0, "end": 6, "font_size": 72})
        api("POST", f"/api/v1/drafts/{draft_id}/texts",
            {"text": "第一条全自动生成的视频", "start": 2, "end": 8, "font_size": 48,
             "y": 200})

        # 5. 渲染
        job = api("POST", "/api/v1/render/tasks", {"draft_id": draft_id})
        if job["status"] != "succeeded":
            print(f"[5/6] 渲染失败: {job.get('error')}")
            return 1
        print(f"[5/6] 渲染完成: {job['file_url']}（{job['duration']}s, {job['width']}x{job['height']}）")

        # 6. ffprobe 验证
        out = DATA_DIR / "renders" / f"{job['task_id']}.mp4"
        info = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                               "format=duration:stream=codec_type,codec_name",
                               "-of", "csv", str(out)], capture_output=True, text=True)
        print(f"[6/6] ffprobe 验证:\n{info.stdout.strip()}")
        print(f"成片路径: {out}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
