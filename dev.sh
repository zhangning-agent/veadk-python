#!/usr/bin/env bash
# 本地启动 VeADK Studio（后端 + 前端开发服务器）
# 后端: http://127.0.0.1:8000
# 前端: http://localhost:5173  （自动代理 /web 等路由到后端）

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 0. 清理已占用的端口
echo "=== 清理端口 8000 / 5173 ==="
kill $(lsof -ti :8000) 2>/dev/null || true
kill $(lsof -ti :5173) 2>/dev/null || true

# 1. 加载 .env 环境变量（如果存在）
if [ -f .env ]; then
  echo "=== 加载 .env 环境变量 ==="
  set -a
  source .env
  set +a
fi

# 2. 启动后端（后台运行）
echo "=== 启动后端 (uv run veadk studio) ==="
uv run veadk studio --host 127.0.0.1 --port 8000 --vite &
BACKEND_PID=$!

# 3. 等待后端启动
echo "=== 等待后端就绪 (端口 8000) ==="
for i in $(seq 1 30); do
  if curl -s http://127.0.0.1:8000/web/ui-config > /dev/null 2>&1; then
    echo "后端已就绪"
    break
  fi
  sleep 1
done

# 4. 启动前端开发服务器
echo "=== 启动前端 (npm run dev) ==="
cd frontend
npm run dev -- --strictPort &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"

# 5. 捕获退出信号，清理子进程
cleanup() {
  echo ""
  echo "=== 关闭服务 ==="
  kill "$BACKEND_PID" 2>/dev/null || true
  kill "$FRONTEND_PID" 2>/dev/null || true
  wait
  echo "已关闭"
}
trap cleanup EXIT INT TERM

echo ""
echo "=== 启动完成 ==="
echo "后端: http://127.0.0.1:8000"
echo "前端: http://localhost:5173"
echo "按 Ctrl+C 停止所有服务"
echo ""

# 等待子进程结束
wait
