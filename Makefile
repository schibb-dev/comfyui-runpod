.PHONY: help up up-minimal up-all down restart ps logs pull build \
        comfy-logs watch-logs sftp-logs \
        ops-up ops-down ops-rm ops-ps ops-logs \
        status-once report-once report-tail \
        history-backfill

# Same defaults as package.json: core stack includes output-sftp (see docker-compose.output-sftp.yml).
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.output-sftp.yml
OPS_SVCS := refresh_run_status report_experiment_queue_status queue_incomplete_experiments queue_ledger ws_event_tap

help:
	@echo "Targets:"
	@echo "  up              Start comfyui + watch_queue + output-sftp"
	@echo "  up-minimal      Start comfyui + watch_queue only (no SFTP container)"
	@echo "  up-all          Start core + output-sftp + ops profile sidecars"
	@echo "  down            Stop full compose project (same files as up)"
	@echo "  restart         Restart comfyui + watch_queue + output-sftp"
	@echo "  ps              Show compose status"
	@echo "  logs            Follow logs (comfyui, watch_queue, output-sftp)"
	@echo "  comfy-logs      Follow comfyui logs"
	@echo "  watch-logs      Follow watch_queue logs"
	@echo "  sftp-logs       Follow output-sftp logs"
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
	$(COMPOSE) up -d comfyui watch_queue output-sftp

up-minimal:
	docker compose up -d comfyui watch_queue

up-all:
	$(COMPOSE) --profile ops up -d comfyui watch_queue output-sftp $(OPS_SVCS)

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart comfyui watch_queue output-sftp

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail 200 comfyui watch_queue output-sftp

pull:
	$(COMPOSE) pull

build:
	$(COMPOSE) build

comfy-logs:
	$(COMPOSE) logs -f --tail 200 comfyui

watch-logs:
	$(COMPOSE) logs -f --tail 200 watch_queue

sftp-logs:
	$(COMPOSE) logs -f --tail 200 output-sftp

ops-up:
	$(COMPOSE) --profile ops up -d $(OPS_SVCS)

ops-down:
	$(COMPOSE) --profile ops stop $(OPS_SVCS)

ops-rm:
	$(COMPOSE) --profile ops rm -fsv $(OPS_SVCS)

ops-ps:
	$(COMPOSE) ps $(OPS_SVCS)

ops-logs:
	$(COMPOSE) logs -f --tail 200 $(OPS_SVCS)

status-once:
	$(COMPOSE) exec -T watch_queue python3 /workspace/ws_scripts/refresh_run_status.py --server http://comfyui:8188

report-once:
	$(COMPOSE) exec -T watch_queue python3 /workspace/ws_scripts/report_experiment_queue_status.py --server http://comfyui:8188 --newest-first --limit 10 --summary-only

report-tail:
	$(COMPOSE) exec -T report_experiment_queue_status sh -lc "tail -n 80 /workspace/output/output/experiments/_status/queue_status.log"

history-backfill:
	$(COMPOSE) exec -T watch_queue python3 /workspace/ws_scripts/backfill_history_from_outputs.py --server http://comfyui:8188 --try-fetch-history --extract-media-metadata
