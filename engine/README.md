# 流光剪影引擎（P0）

对标「流光剪辑」的自建云端视频生产引擎，P0 阶段：草稿 CRUD + FFmpeg 同步渲染 + TTS。
架构设计见项目根目录《个人云端视频生产系统架构设计.html》。

## 目录

- `app/` 引擎代码：`models.py`（时间线模型）、`store.py`（持久化）、`compiler.py`（FFmpeg 编译器）、`tts.py`（TTS 适配）、`main.py`（FastAPI）、`probe.py`、`config.py`
- `tests/` pytest（模型 / 编译器命令 / API 集成与渲染冒烟）
- `scripts/demo_e2e.py` 一键端到端演示
- `scripts/export_openapi.py` 导出扣子插件 schema
- `coze_plugin/openapi.json` 扣子可导入的 OpenAPI 3.0

## 快速开始

```bash
cd engine
./.venv/bin/pip install -r requirements.txt   # 已装可跳过
./.venv/bin/uvicorn app.main:app --port 8390  # 起服务
# 另开终端
./.venv/bin/python scripts/demo_e2e.py        # 一键端到端（自带起停服务）
./.venv/bin/python -m pytest tests/ -q        # 测试
```

## Docker 部署

```bash
cd engine
docker compose up -d --build        # 构建并启动
curl http://127.0.0.1:8390/api/v1/health
docker compose logs -f              # 看日志
docker compose down                 # 停止（数据保留在 engine-data 卷）
```

镜像基于 `python:3.14-slim`，自带 ffmpeg 与 Noto CJK 中文字体；草稿/素材/成片持久化在 `engine-data` 卷。
注意：容器为 Linux 环境，`mac_say` TTS 兜底不可用，TTS 仅 edge_tts（需联网）。

## API 一览（/api/v1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /health | 健康检查（含字体就绪状态） |
| POST | /drafts | 创建草稿 {name,width,height,fps} |
| GET | /drafts | 草稿列表 |
| GET | /drafts/{id} | 查询完整时间线（query_script） |
| DELETE | /drafts/{id} | 删除草稿 |
| POST | /drafts/{id}/videos·images·audios·texts | 添加素材/文字，body 单个或数组批量 |
| PATCH | /drafts/{id}/{resource}/{segment_id} | 修改时间/变换/样式 |
| DELETE | /drafts/{id}/{resource}/{segment_id} | 删除片段 |
| GET | /media/probe?url= | 时长/分辨率/音轨探测 |
| POST | /ai/tts | 语音合成 {text,voice?,engine?}，失败自动降级 |
| POST | /render/tasks | 同步渲染 {draft_id,crf?,preset?} |
| GET | /render/tasks/{id} | 渲染任务状态 |
| GET | /files/... | 成片/音频/素材静态下载 |

## P0 边界（P1+ 待办）

- 不支持：关键帧、转场、蒙版、特效、贴纸、滤镜、字幕 ASS、异步渲染队列、多机 Worker
- 渲染为同步阻塞（适合小样）；成片 H.264+AAC MP4
- 文字层：Pillow 预渲染透明 PNG 后 overlay（不依赖 ffmpeg 的 drawtext/libfreetype，规避云端 ffmpeg 构建差异），样式支持描边/半透明色
- TTS：edge_tts（默认，需联网）失败自动降级 mac_say（系统合成）
- 文字渲染依赖系统字体（config.py 自动探测 PingFang 等）
- 视频素材自带音轨不参与混音（仅 audio 轨）
