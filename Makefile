.PHONY: help up down restart ps logs pull build \
        ops-up ops-down ops-rm ops-ps ops-logs \
        comfy-logs watch-logs \
        status-once report-once report-tail \
        history-backfill

help:
	@echo "Targets:"
	@echo "  up              Start core stack (comfyui + watch_queue)"
	@echo "  down            Stop core stack"
	@echo "  restart         Restart core services"
	@echo "  ps              Show compose status"
	@echo "  logs            Follow logs (all services)"
	@echo "  comfy-logs      Follow comfyui logs"
	@echo "  watch-logs      Follow watch_queue logs"
	@echo ""
	@echo "  ops-up          Start ops profile sidecars"
	@echo "  ops-down        Stop ops profile sidecars"
	@echo "  ops-rm          Remove ops profile sidecars"
	@echo "  ops-ps          Show ops profile status"
	@echo "  ops-logs        Follow ops profile logs"
	@echo ""
	@echo "  status-once     Write status.json once (inside container)"
	@echo "  report-once     Print queue status summary once (inside container)"
	@echo "  report-tail     Tail queue_status.log (inside container)"
	@echo "  history-backfill Backfill missing history.json from outputs (inside container)"

up:
	docker compose up -d comfyui watch_queue

down:
	docker compose down

restart:
	docker compose restart comfyui watch_queue

ps:
	docker compose ps

logs:
	docker compose logs -f --tail 200

pull:
	docker compose pull

build:
	docker compose build

comfy-logs:
	docker compose logs -f --tail 200 comfyui

watch-logs:
	docker compose logs -f --tail 200 watch_queue

ops-up:
	docker compose --profile ops up -d refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap

ops-down:
	docker compose --profile ops stop refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap

ops-rm:
	docker compose --profile ops rm -fsv refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap

ops-ps:
	docker compose ps refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap

ops-logs:
	docker compose logs -f --tail 200 refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap

status-once:
	docker compose exec -T watch_queue python3 /workspace/ws_scripts/refresh_run_status.py --server http://comfyui:8188

report-once:
	docker compose exec -T watch_queue python3 /workspace/ws_scripts/report_experiment_queue_status.py --server http://comfyui:8188 --newest-first --limit 10 --summary-only

report-tail:
	docker compose exec -T report_experiment_queue_status sh -lc "tail -n 80 /workspace/output/output/experiments/_status/queue_status.log"

history-backfill:
	docker compose exec -T watch_queue python3 /workspace/ws_scripts/backfill_history_from_outputs.py --server http://comfyui:8188 --try-fetch-history --extract-media-metadata

