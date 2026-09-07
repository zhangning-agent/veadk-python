#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.managed-agents.yml"

load_env() {
    if [ ! -f "$SCRIPT_DIR/.env" ]; then
        echo "Missing $SCRIPT_DIR/.env" >&2
        exit 1
    fi
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    export TASK_SERVER_BASE_URL="${TASK_SERVER_BASE_URL:-$ANTHROPIC_BASE_URL}"
    export TASK_SERVER_API_KEY="${TASK_SERVER_API_KEY:-$ANTHROPIC_ENVIRONMENT_KEY}"
    export TASK_SERVER_ACCOUNT_ID="${TASK_SERVER_ACCOUNT_ID:-${X_TOP_ACCOUNT_ID:-}}"
    export MA_SERVER_API_TOKEN="${MA_SERVER_API_TOKEN:-$ANTHROPIC_ENVIRONMENT_KEY}"
    export POSTGRES_PORT="${POSTGRES_PORT:-55432}"
    export MA_SERVER_PORT="${MA_SERVER_PORT:-18081}"
    export MANAGED_AGENTS_GATEWAY_PORT="${MANAGED_AGENTS_GATEWAY_PORT:-18080}"
    export VEADK_MANAGED_SESSION_DB_URL="${VEADK_MANAGED_SESSION_DB_URL:-postgresql+asyncpg://veadk:veadk@127.0.0.1:${POSTGRES_PORT}/veadk}"
}

up() {
    load_env
    docker compose -f "$COMPOSE_FILE" up -d --build postgres ma-server gateway
    postgres_ready=false
    for _ in $(seq 1 30); do
        if docker compose -f "$COMPOSE_FILE" exec -T postgres pg_isready -U veadk -d veadk >/dev/null; then
            postgres_ready=true
            break
        fi
        sleep 1
    done
    if [ "$postgres_ready" != true ]; then
        echo "Managed Agents PostgreSQL did not become ready" >&2
        exit 1
    fi
    ready=false
    for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:${MANAGED_AGENTS_GATEWAY_PORT}/health" >/dev/null; then
            ready=true
            break
        fi
        sleep 1
    done
    if [ "$ready" != true ]; then
        echo "Managed Agents gateway did not become ready" >&2
        exit 1
    fi
    echo "Gateway: http://127.0.0.1:${MANAGED_AGENTS_GATEWAY_PORT}"
    echo "Official worker API: http://127.0.0.1:${MA_SERVER_PORT}"
    echo "PostgreSQL: 127.0.0.1:${POSTGRES_PORT}/veadk"
}

down() {
    load_env
    docker compose -f "$COMPOSE_FILE" down
}

case "${1:-up}" in
    up) up ;;
    down) down ;;
    status)
        load_env
        docker compose -f "$COMPOSE_FILE" ps
        curl -fsS "http://127.0.0.1:${MANAGED_AGENTS_GATEWAY_PORT}/health"
        echo
        ;;
    test)
        up
        shift
        exec env ANTHROPIC_BASE_URL="http://127.0.0.1:${MA_SERVER_PORT}" \
            VEADK_MANAGED_SESSION_DB_URL="$VEADK_MANAGED_SESSION_DB_URL" \
            "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/distributed_agent_loop_test.py" "$@"
        ;;
    *) echo "usage: $0 {up|down|status|test}" >&2; exit 2 ;;
esac
