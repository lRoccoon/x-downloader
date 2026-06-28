#!/usr/bin/env bash
# 不依赖 Docker，直接在宿主机用 venv 运行（适用于 docker 运行时不可用时）。
# 用法：
#   ./run-local.sh                 # 前台运行
#   nohup ./run-local.sh > local.log 2>&1 &   # 后台常驻
set -euo pipefail
cd "$(dirname "$0")"

# 从 .env 读取 APP_PASSWORD / SECRET_KEY / THREADS（若存在）
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

export DOWNLOAD_DIR="$PWD/downloads"     # 视频保存到项目下 downloads/
export DATA_DIR="$PWD/data"              # cookies 与任务库
export THREADS="${THREADS:-16}"
# 本地运行直连宿主机 clash（不是容器里的 host.docker.internal）
export PROXY="http://127.0.0.1:7890"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "venv 未就绪，请先执行： python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "🎬 X 视频下载器启动中： http://localhost:8000  （密码: ${APP_PASSWORD:-changeme}）"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
