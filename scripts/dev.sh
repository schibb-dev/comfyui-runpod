#!/bin/bash

case "$1" in
    start)
        echo "🚀 Starting ComfyUI..."
        docker-compose up -d
        echo "✅ ComfyUI running at http://localhost:8188"
        ;;
    stop)
        echo "🛑 Stopping ComfyUI..."
        docker-compose down
        ;;
    restart)
        echo "🔄 Restarting ComfyUI..."
        docker-compose restart
        ;;
    logs)
        docker-compose logs -f
        ;;
    shell)
        echo "🐚 Opening shell in container..."
        docker-compose exec comfyui bash
        ;;
    rebuild)
        echo "🔨 Rebuilding..."
        docker-compose build
        ;;
    *)
        echo "Usage: ./scripts/dev.sh {start|stop|restart|logs|shell|rebuild}"
        exit 1
        ;;
esac

