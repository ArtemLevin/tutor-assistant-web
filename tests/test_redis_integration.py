from __future__ import annotations

import os

import pytest
from redis import Redis

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_REDIS_URL,
    reason="TEST_REDIS_URL is required for Redis integration tests",
)


def test_redis_broker_is_reachable_and_durable_queues_are_declared(monkeypatch):
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("TASK_EAGER", "false")
    from tutor_assistant_web.worker import celery_app

    client = Redis.from_url(TEST_REDIS_URL)
    try:
        assert client.ping() is True
        queues = {queue.name for queue in celery_app.conf.task_queues}
        assert queues == {"transcription", "materials", "delivery", "maintenance"}
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.worker_prefetch_multiplier == 1
    finally:
        client.flushdb()
        client.close()
