FROM python:3.12-slim

LABEL org.opencontainers.image.title="DoOver 错题本" \
      org.opencontainers.image.description="家用局域网错题管理系统（三四年级 · 数语英三科）"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖单独分层，源码改动不触发依赖重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 源码 + 字体（字体随源码进镜像，构建不依赖外网下载字体）
COPY . .

# 数据目录全部落在挂载卷（db / photos / thumbs / backups / exports）
VOLUME ["/app/data"]

EXPOSE 8200

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8200/api/health',timeout=8)"

CMD ["python", "server.py"]
