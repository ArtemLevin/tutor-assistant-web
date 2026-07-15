UV ?= uv

.PHONY: help sync sync-transcription migrate run worker worker-transcription worker-materials worker-delivery worker-maintenance beat outbox tasks artifacts-init artifacts-verify artifacts-migrate test test-postgres test-minio lint format security check schema-check diagnose docker-up docker-down

help:
	@echo "sync        Install all dependencies with uv"
	@echo "run         Start the web app"
	@echo "migrate     Upgrade the database to the latest revision"
	@echo "worker      Start the Celery worker"
	@echo "worker-*    Start one dedicated Celery queue worker"
	@echo "beat        Start the transactional outbox scheduler"
	@echo "outbox      Dispatch pending outbox events once"
	@echo "artifacts-* Configure, verify or migrate S3 artifacts"
	@echo "check       Run lint and tests"
	@echo "security    Run static and dependency security scans"
	@echo "test-postgres Run PostgreSQL integration tests"
	@echo "schema-check Validate the committed evidence schema contract"
	@echo "diagnose    Print runtime diagnostics"
	@echo "docker-up   Start app, worker, PostgreSQL and Redis"

sync:
	$(UV) sync --extra dev

sync-transcription:
	$(UV) sync --extra dev --extra transcription

run:
	$(UV) run tutor-assistant-web

migrate:
	$(UV) run alembic upgrade head

worker:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO --queues=transcription,materials,delivery,maintenance

worker-transcription:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO --queues=transcription --hostname=transcription@%h

worker-materials:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO --queues=materials --hostname=materials@%h

worker-delivery:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO --queues=delivery --hostname=delivery@%h

worker-maintenance:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app worker --loglevel=INFO --queues=maintenance --hostname=maintenance@%h

beat:
	$(UV) run celery -A tutor_assistant_web.worker.celery_app beat --loglevel=INFO

outbox:
	$(UV) run python -c "from tutor_assistant_web.worker import dispatch_outbox_task; print(dispatch_outbox_task())"

tasks:
	$(UV) run tutor-assistant-ops list --organization $(ORGANIZATION)

artifacts-init:
	$(UV) run tutor-assistant-artifacts configure-bucket

artifacts-verify:
	$(UV) run tutor-assistant-artifacts verify --limit 500

artifacts-migrate:
	$(UV) run tutor-assistant-artifacts migrate-local --limit 500

test:
	$(UV) run pytest

test-postgres:
	$(UV) run pytest tests/test_postgres_integration.py

test-minio:
	$(UV) run pytest tests/test_minio_integration.py

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

security:
	$(UV) run bandit -r src -ll
	$(UV) run pip-audit --skip-editable

check: lint security test

schema-check:
	$(UV) run pytest tests/test_materials_factory.py -k schema

diagnose:
	@$(UV) --version
	@$(UV) run python --version
	@$(UV) run python -c "from tutor_assistant_web.config import get_settings; s=get_settings(); print({'env':s.app_env,'database':s.database_url.split(':',1)[0],'bbb_demo':s.bbb_demo_mode,'bbb_configured':bool(s.bbb_base_url and s.bbb_secret),'task_eager':s.task_eager,'document_engine':s.document_engine_provider,'artifact_storage':s.artifact_storage_provider,'artifact_bucket':s.artifact_s3_bucket})"

docker-up:
	docker compose up --build

docker-down:
	docker compose down
