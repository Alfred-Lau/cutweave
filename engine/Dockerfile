# CutWeave P0 —— Linux 容器（Debian slim + ffmpeg + Noto CJK 字体）
# 拉取官方镜像困难时，可先从镜像加速源获取并打 tag，例如：
#   docker pull docker.m.daocloud.io/library/python:3.13-slim
#   docker tag docker.m.daocloud.io/library/python:3.13-slim python:3.13-slim
FROM python:3.13-slim

# 中国大陆构建加速：--build-arg USE_CN_MIRROR=true（切换 apt / pypi 到阿里云镜像）
ARG USE_CN_MIRROR=false

RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' \
            /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY app/ ./app/

# 数据目录（草稿/素材/成片）挂载到卷；ffmpeg 路径指向 apt 版
ENV LG_DATA_DIR=/data \
    LG_FFMPEG=/usr/bin/ffmpeg \
    LG_FFPROBE=/usr/bin/ffprobe \
    PYTHONUNBUFFERED=1

EXPOSE 8390
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8390/api/v1/health', timeout=4).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8390"]
