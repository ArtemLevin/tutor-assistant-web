from __future__ import annotations

import argparse
import json

from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.durability import DurableJobService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Durable worker operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, identifier in (
        ("retry", "job_id"),
        ("cancel", "job_id"),
        ("resend-outbox", "event_id"),
    ):
        item = subparsers.add_parser(command)
        item.add_argument(identifier)
        item.add_argument("--organization", required=True)
    recover = subparsers.add_parser("recover")
    recover.add_argument("--limit", type=int, default=100)
    listing = subparsers.add_parser("list")
    listing.add_argument("--organization", required=True)
    listing.add_argument("--limit", type=int, default=200)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    database = Database.from_settings(settings)
    service = DurableJobService(
        database,
        lease_seconds=settings.job_lease_seconds,
        max_attempts=settings.workflow_max_attempts,
        retry_base_seconds=settings.workflow_retry_base_seconds,
        retry_max_seconds=settings.workflow_retry_max_seconds,
    )
    try:
        if args.command == "retry":
            result = service.retry_manually(args.organization, args.job_id)
            print(json.dumps({"job_id": result.id, "status": result.status}))
        elif args.command == "cancel":
            result = service.cancel(args.organization, args.job_id)
            print(json.dumps({"job_id": result.id, "status": result.status}))
        elif args.command == "resend-outbox":
            result = service.resend_outbox(args.organization, args.event_id)
            print(json.dumps({"event_id": result.id, "status": result.status}))
        elif args.command == "recover":
            print(json.dumps({"recovered": service.recover_expired(args.limit)}))
        elif args.command == "list":
            jobs, events = service.operations(args.organization, args.limit)
            print(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": item.id,
                                "kind": item.kind,
                                "status": item.status,
                                "stage": item.stage,
                                "attempts": item.attempt_count,
                                "error": item.error,
                            }
                            for item in jobs
                        ],
                        "outbox": [
                            {
                                "id": item.id,
                                "topic": item.topic,
                                "attempts": item.attempts,
                                "error": item.last_error,
                            }
                            for item in events
                        ],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
