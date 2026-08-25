
#!/bin/bash
set -a
source "$(dirname "$0")/.env"
set +a

# 映射 sandbox 变量到 ant CLI 期望的变量名
export ANTHROPIC_ENVIRONMENT_ID="${ANTHROPIC_ENVIRONMENT_ID:-$SANDBOX_ENVIRONMENT_ID}"
export ANTHROPIC_ENVIRONMENT_KEY="${ANTHROPIC_ENVIRONMENT_KEY:-}"
export ANTHROPIC_WORKER_ID="${ANTHROPIC_WORKER_ID:-}"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"

# 优先查找 anthropic-cli 二进制
ANT_BIN=""
if [ -x "/Users/bytedance/github/agent-ma/anthropic-cli/bin/ant" ]; then
    ANT_BIN="/Users/bytedance/github/agent-ma/anthropic-cli/bin/ant"
elif [ -x "/tmp/ant" ]; then
    ANT_BIN="/tmp/ant"
elif command -v ant >/dev/null 2>&1 && ant beta:worker poll --help >/dev/null 2>&1; then
    ANT_BIN="ant"
else
    echo "未找到 anthropic-cli 二进制，正在自动编译..."
    (cd /Users/bytedance/github/agent-ma/anthropic-cli && go build -o bin/ant ./cmd/ant)
    ANT_BIN="/Users/bytedance/github/agent-ma/anthropic-cli/bin/ant"
fi

WORKDIR="${ANTHROPIC_WORKDIR:-/Users/bytedance/omaworkspace}"
mkdir -p "$WORKDIR"

echo "使用 Anthropic CLI: $ANT_BIN"
echo "环境 ID: $ANTHROPIC_ENVIRONMENT_ID"
echo "Base URL: $ANTHROPIC_BASE_URL"
echo "工作区: $WORKDIR"

"$ANT_BIN" beta:worker poll --workdir "$WORKDIR"