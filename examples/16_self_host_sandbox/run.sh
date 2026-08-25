#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Ensure veadk is always imported directly from this local repository
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

# Check .env existence
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "⚠️  未检测到 $SCRIPT_DIR/.env 配置文件。"
    if [ -f "$SCRIPT_DIR/.env.example" ]; then
        echo "💡 提示：请先执行 'cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env' 并配置相应密钥。"
    fi
fi

cd "$SCRIPT_DIR"

# Prioritize local repo virtualenv if present, otherwise invoke via uv or system python
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    exec "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/main.py" "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --directory "$REPO_ROOT" --extra sandbox python "$SCRIPT_DIR/main.py" "$@"
else
    exec python3 "$SCRIPT_DIR/main.py" "$@"
fi
