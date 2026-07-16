#!/usr/bin/env bash
# Development helper script for PointsBot
# Provides a single-service HA environment for integration testing.

set -e

COMPOSE_FILES="-f docker-compose.yml"
PLATFORM=$(uname -s)

if [[ "$PLATFORM" == "Linux" ]]; then
    echo "Detected Linux"
    # On Linux Docker networking, 0.0.0.0 binding in the container reaches the
    # host directly without extra compose overrides.
fi

CMD=${1:-up}
shift || true

case "$CMD" in
    up)
        # Initialize/update submodule if needed
        if [ ! -d "frontend/src" ]; then
            echo "Initializing frontend submodule..."
            git submodule update --init --recursive
        fi

        mkdir -p dev-config
        echo "Starting PointsBot development environment..."
        docker compose $COMPOSE_FILES up -d "$@"
        echo "Home Assistant: http://localhost:8123"
        echo "View logs: ./dev.sh logs"
        ;;
    down)
        echo "Stopping PointsBot development environment..."
        docker compose $COMPOSE_FILES down "$@"
        ;;
    logs)
        docker compose $COMPOSE_FILES logs -f "$@"
        ;;
    restart)
        docker compose $COMPOSE_FILES restart "$@"
        ;;
    rebuild)
        docker compose $COMPOSE_FILES up -d --build "$@"
        ;;
    shell)
        echo "Opening shell in Home Assistant container..."
        docker compose exec homeassistant bash
        ;;
    ps)
        docker compose $COMPOSE_FILES ps "$@"
        ;;
    *)
        echo "PointsBot Development Helper"
        echo ""
        echo "Usage: ./dev.sh [command]"
        echo ""
        echo "Commands:"
        echo "  up          Start HA (default); creates dev-config/ if absent"
        echo "  down        Stop HA"
        echo "  logs        Tail container logs (optionally: ./dev.sh logs homeassistant)"
        echo "  restart     Restart HA container"
        echo "  rebuild     Force-rebuild and restart"
        echo "  shell       Open a bash shell inside the HA container"
        echo "  ps          Show running containers"
        ;;
esac
