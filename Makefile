UV ?= uv

.PHONY: help sync run worker test lint format check diagnose docker-up docker-down

help:
	@echo "sync        Install all dependencies with uv"
	@echo "run         Start the web app"
	@echo "worker      Start the Celery worker"
	@echo "check       Run lint and tests"
	@echo "diagnose    Print runtime diagnostics"
	@echo "docker-up   Start app, worker, PostgreSQL and Redis"

sync:
	$(UV) sync --extra dev

run:
	$(UV) run tutor-assistant-web

worker:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

check: lint test

diagnose:
	@$(UV) --version
	@$(UV) run python --version
	@$(UV) run python -c "from tutor_assistant_web.config import get_settings; s=get_settings(); print({'env':s.app_env,'database':s.database_url.split(':',1)[0],'bbb_demo':s.bbb_demo_mode,'bbb_configured':bool(s.bbb_base_url and s.bbb_secret),'task_eager':s.task_eager})"

docker-up:
	docker compose up --build

docker-down:
	docker compose down

