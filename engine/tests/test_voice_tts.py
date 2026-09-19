"""TTS 多引擎（edge_tts / cosyvoice / volcengine）与声音克隆端点测试。

不访问外部网络：云端引擎只测缺凭证时的快速失败路径与请求模型校验。
"""
import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tts import split_text


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------- split_text 长文本分段 ----------

def test_split_text_short_keeps_single():
    assert split_text("你好世界") == ["你好世界"]


def test_split_text_by_sentence():
    text = "第一句话。第二句话！第三句话？第四句话；第五句话\n第六句话"
    segs = split_text(text, max_chars=6)
    assert all(len(s) <= 6 for s in segs)
    # 分段允许丢弃换行等空白，但不丢有效字符
    assert "".join(segs) == text.replace("\n", "")


def test_split_text_fallback_comma():
    text = "短句,再来一个,继续,结束"
    segs = split_text(text, max_chars=5)
    assert all(len(s) <= 5 for s in segs)
    assert "".join(segs) == text


def test_split_text_hard_cut():
    text = "a" * 10
    assert split_text(text, max_chars=3) == ["aaa", "aaa", "aaa", "a"]


def test_split_text_long_sentence_hard_cut():
    # 无标点长句内部也要能切
    segs = split_text("abcdefghij" * 3, max_chars=7)
    assert all(len(s) <= 7 for s in segs)
    assert "".join(segs) == "abcdefghij" * 3


# ---------- TTS 端点 ----------

def test_tts_rejects_unknown_engine(client):
    r = client.post("/api/v1/ai/tts", json={"text": "你好", "engine": "nope"})
    assert r.status_code == 422


def test_tts_cosyvoice_missing_key_fast_fail(client):
    # 未配置 DASHSCOPE_API_KEY 时应快速失败而不是访问外网
    r = client.post("/api/v1/ai/tts", json={"text": "你好", "engine": "cosyvoice"})
    assert r.status_code == 502
    assert "DASHSCOPE_API_KEY" in r.json()["detail"]


def test_tts_volcengine_missing_creds_fast_fail(client):
    r = client.post("/api/v1/ai/tts",
                    json={"text": "你好", "engine": "volcengine",
                          "voice": "zh_female_cmlccs_moon_bigtts"})
    assert r.status_code == 502
    assert "VOLC_TTS_APPID" in r.json()["detail"]


def test_tts_volcengine_missing_voice(client, monkeypatch):
    # 有凭证但既无 voice 请求参数也无缺省音色 → 明确报错
    import app.config as cfg
    monkeypatch.setattr(cfg, "VOLC_TTS_APPID", "test_app")
    monkeypatch.setattr(cfg, "VOLC_TTS_TOKEN", "test_token")
    r = client.post("/api/v1/ai/tts", json={"text": "你好", "engine": "volcengine"})
    assert r.status_code == 502
    assert "voice" in r.json()["detail"]


# ---------- 声音克隆端点（阿里 CosyVoice 路径，输入校验） ----------

def test_voice_clone_requires_name(client):
    r = client.post("/api/v1/ai/voice/clone", json={"audio_base64": "aGVsbG8="})
    assert r.status_code == 400


def test_voice_clone_rejects_bad_base64(client):
    r = client.post("/api/v1/ai/voice/clone",
                    json={"name": "myvoice", "audio_base64": "!!!!不是base64"})
    assert r.status_code == 400


def test_voice_clone_rejects_empty_audio(client):
    r = client.post("/api/v1/ai/voice/clone",
                    json={"name": "myvoice", "audio_base64": base64.b64encode(b"").decode()})
    assert r.status_code == 400


def test_voice_clone_missing_dashscope_key(client):
    payload = {"name": "myvoice", "audio_base64": base64.b64encode(b"12345678").decode()}
    r = client.post("/api/v1/ai/voice/clone", json=payload)
    assert r.status_code in (400, 502)


def test_voices_list(client):
    r = client.get("/api/v1/ai/voices")
    assert r.status_code == 200
    assert isinstance(r.json()["voices"], list)
