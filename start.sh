#!/bin/bash
# DoOver 错题本 · 本机一键启动（Mac / Linux）
# 自动创建虚拟环境并安装依赖，然后启动服务
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "✗ 未找到 python3，请先安装 Python 3.10+"; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "[DoOver] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, PIL, reportlab, apscheduler" 2>/dev/null; then
  echo "[DoOver] 安装依赖 ..."
  .venv/bin/pip install -q -r requirements.txt
fi

echo "[DoOver] 启动 → http://localhost:8200 （手机/平板连同一 Wi-Fi 访问 http://<本机IP>:8200）"
exec .venv/bin/python server.py
