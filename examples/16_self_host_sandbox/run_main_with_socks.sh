#!/bin/bash
# Run the Self-Hosted Sandbox demo with SOCKS proxy support.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "错误：未找到虚拟环境解释器 $VENV_PY" >&2
    echo "请先在仓库根目录创建 .venv 并执行：uv pip install -e ." >&2
    exit 1
fi

if ! "$VENV_PY" -c 'import socksio' >/dev/null 2>&1; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "错误：当前环境缺少 socksio，且未找到 uv 命令。" >&2
        echo "请安装 uv 后重试。" >&2
        exit 1
    fi

    echo "未检测到 socksio，正在为项目虚拟环境安装 SOCKS 支持..."
    uv pip install --python "$VENV_PY" 'httpx2[socks]'
fi

cd "$SCRIPT_DIR"
exec "$VENV_PY" main.py "$@"
