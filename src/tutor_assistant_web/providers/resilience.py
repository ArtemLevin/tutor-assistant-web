from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import httpx


class CircuitOpenError(RuntimeError):
    pass


def is_transient_external_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            return current.response.status_code == 429 or current.response.status_code >= 500
        current = current.__cause__
    return False


@dataclass(frozen=True)
class CircuitSnapshot:
    state: str
    failures: int
    retry_after: float


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 60,
        clock=time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_progress = False
        self._lock = threading.Lock()

    @contextmanager
    def guard(self):
        self.before_call()
        try:
            yield
        except Exception as exc:
            self.record_failure(exc)
            raise
        else:
            self.record_success()

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            elapsed = self.clock() - self._opened_at
            if elapsed < self.recovery_seconds:
                raise CircuitOpenError(
                    f"{self.name} circuit is open for {self.recovery_seconds - elapsed:.1f}s"
                )
            if self._probe_in_progress:
                raise CircuitOpenError(f"{self.name} circuit recovery probe is in progress")
            self._probe_in_progress = True

    def record_failure(self, error: Exception) -> None:
        with self._lock:
            self._probe_in_progress = False
            if not is_transient_external_error(error):
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self.clock()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_progress = False

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            retry_after = 0.0
            state = "closed"
            if self._opened_at is not None:
                retry_after = max(0.0, self.recovery_seconds - (self.clock() - self._opened_at))
                state = "open" if retry_after else "half_open"
            return CircuitSnapshot(state=state, failures=self._failures, retry_after=retry_after)
