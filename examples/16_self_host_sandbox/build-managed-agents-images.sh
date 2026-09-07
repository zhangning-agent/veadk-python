#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SDK_ROOT="${MANAGED_AGENTS_SDK_ROOT:-$REPO_ROOT/../agent-ma/anthropic-sdk-python}"
if [ ! -d "$SDK_ROOT/src/anthropic" ]; then
    SDK_ROOT="${MANAGED_AGENTS_SDK_ROOT:-$REPO_ROOT/../anthropic-sdk-python}"
fi
REGISTRY="${MANAGED_AGENTS_REGISTRY:-zn-datapath-cn-beijing.cr.volces.com/sandbox}"
TAG="${1:-$(date -u +%Y%m%d)-$(git -C "$REPO_ROOT" rev-parse --short HEAD)}"
OUTPUT_DIR="$SCRIPT_DIR/.docker-dist"

if [ ! -d "$SDK_ROOT/src/anthropic" ]; then
    echo "Managed Agents SDK checkout not found: $SDK_ROOT" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'anthropic-*.whl' -delete
uv build --wheel --out-dir "$OUTPUT_DIR" "$SDK_ROOT"

gateway_image="$REGISTRY/veadk-managed-agents-gateway:$TAG"
worker_image="$REGISTRY/veadk-managed-agents-worker:$TAG"

docker buildx build --platform linux/amd64 --push \
    --file "$SCRIPT_DIR/Dockerfile.managed-agents-gateway" \
    --tag "$gateway_image" \
    "$REPO_ROOT"
docker buildx build --platform linux/amd64 --push \
    --file "$SCRIPT_DIR/Dockerfile.managed-agents-worker" \
    --tag "$worker_image" \
    "$REPO_ROOT"

printf 'GATEWAY_IMAGE=%s\nWORKER_IMAGE=%s\n' "$gateway_image" "$worker_image"
