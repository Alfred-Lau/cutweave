"""声音复刻：参考音频 → OSS 签名 URL 中转 → 百炼 CosyVoice 复刻 → 本地音色登记。

流程（复刻一次，之后 TTS 直接引用 voice_id）：
1. 接收 base64 参考音频（10~20s 干净人声，≤10MB）
2. 上传到用户 OSS（ECS 内网 endpoint 免流量费）→ 生成 1h 签名 URL
3. 调百炼 voice-enrollment 创建音色（target_model 与后续合成一致）
4. 删除 OSS 对象与本地临时文件（音频不留存）
5. 登记到 data/voices.json（voice_id/name/model/时间）
"""
from __future__ import annotations

import base64
import binascii
import json
import time
import uuid

import httpx

from .config import (COSYVOICE_MODEL, DASHSCOPE_API_KEY, DASHSCOPE_WORKSPACE_ID,
                     DATA_DIR, OSS_ACCESS_KEY_ID, OSS_BUCKET, OSS_ENDPOINT,
                     OSS_ACCESS_KEY_SECRET, OSS_OBJECT_PREFIX)

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 百炼复刻音频上限 10MB
SIGN_TTL = 3600


class VoiceCloneError(Exception):
    pass


VOICES_INDEX = DATA_DIR / "voices.json"


def _customization_endpoint() -> str:
    if DASHSCOPE_WORKSPACE_ID:
        return (f"https://{DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com"
                "/api/v1/services/audio/tts/customization")
    return "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
            "Content-Type": "application/json"}


def _check_oss() -> None:
    missing = [k for k, v in (
        ("OSS_BUCKET", OSS_BUCKET), ("OSS_ENDPOINT", OSS_ENDPOINT),
        ("OSS_ACCESS_KEY_ID", OSS_ACCESS_KEY_ID),
        ("OSS_ACCESS_KEY_SECRET", OSS_ACCESS_KEY_SECRET),
    ) if not v]
    if missing:
        raise VoiceCloneError(
            "OSS 未配置（复刻音频需公网 URL 中转）：缺 " + "、".join(missing))


def _oss_bucket():
    import oss2  # 延迟导入：仅在配置齐全时需要

    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)


def _oss_upload_and_sign(local: Path) -> tuple[str, str]:
    bucket = _oss_bucket()
    key = f"{OSS_OBJECT_PREFIX}{uuid.uuid4().hex[:12]}{local.suffix}"
    bucket.put_object(key, local.read_bytes())
    url = bucket.sign_url("GET", key, SIGN_TTL)
    return key, url


def _oss_delete(key: str) -> None:
    try:
        _oss_bucket().delete_object(key)
    except Exception:  # 中转对象删除失败不影响主流程
        pass


def _clone_api(audio_url: str, target_model: str, prefix: str) -> str:
    payload = {"model": "voice-enrollment",
               "input": {"action": "create_voice", "target_model": target_model,
                         "prefix": prefix, "url": audio_url}}
    try:
        r = httpx.post(_customization_endpoint(), json=payload,
                       headers=_auth_headers(), timeout=90)
    except httpx.HTTPError as e:
        raise VoiceCloneError(f"复刻接口网络错误: {e}")
    try:
        data = r.json()
    except ValueError:
        raise VoiceCloneError(f"复刻接口响应异常: HTTP {r.status_code}")
    if r.status_code != 200:
        msg = str(data.get("message") or data)[:300]
        raise VoiceCloneError(f"复刻接口失败 HTTP {r.status_code}: {msg}")
    out = data.get("output", {})
    voice_id = out.get("voice_id") or out.get("voice")  # Qwen 系列字段名不同，兼容
    if not voice_id:
        raise VoiceCloneError(f"复刻响应缺少音色 ID: {str(data)[:300]}")
    return voice_id


def clone_voice(name: str, audio_base64: str, model: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise VoiceCloneError("name 不能为空")
    try:
        raw = base64.b64decode(audio_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise VoiceCloneError("audio_base64 不是有效 base64")
    if not raw:
        raise VoiceCloneError("音频内容为空")
    if len(raw) > MAX_AUDIO_BYTES:
        raise VoiceCloneError("音频超过 10MB 上限")
    if not DASHSCOPE_API_KEY:
        raise VoiceCloneError("未配置 DASHSCOPE_API_KEY（百炼控制台创建后设置环境变量）")
    _check_oss()
    target = (model or COSYVOICE_MODEL).strip()

    tmp = DATA_DIR / "tts" / f"clone_src_{uuid.uuid4().hex[:12]}.mp3"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    key: str | None = None
    try:
        key, url = _oss_upload_and_sign(tmp)
        voice_id = _clone_api(url, target, prefix="cw" + uuid.uuid4().hex[:6])
    finally:
        if key:
            _oss_delete(key)
        tmp.unlink(missing_ok=True)

    rec = {"voice_id": voice_id, "name": name, "target_model": target,
           "created_at": round(time.time(), 3)}
    recs = list_voices()
    recs.append(rec)
    VOICES_INDEX.write_text(json.dumps(recs, ensure_ascii=False, indent=2))
    return rec


def list_voices() -> list[dict]:
    if not VOICES_INDEX.exists():
        return []
    try:
        return json.loads(VOICES_INDEX.read_text())
    except Exception:
        return []


def delete_voice(voice_id: str) -> dict:
    recs = list_voices()
    if not any(r["voice_id"] == voice_id for r in recs):
        raise VoiceCloneError(f"音色不存在: {voice_id}")
    if DASHSCOPE_API_KEY:
        try:  # 平台侧删除尽力而为；本地登记必定移除
            httpx.post(_customization_endpoint(),
                       json={"model": "voice-enrollment",
                             "input": {"action": "delete_voice", "voice_id": voice_id}},
                       headers=_auth_headers(), timeout=30)
        except Exception:
            pass
    VOICES_INDEX.write_text(json.dumps(
        [r for r in recs if r["voice_id"] != voice_id], ensure_ascii=False, indent=2))
    return {"deleted": voice_id}
