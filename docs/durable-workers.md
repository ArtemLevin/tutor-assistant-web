# Durable workers и transactional outbox

## Гарантии

Состояние business job и outbox хранится в PostgreSQL. Redis служит устойчивым транспортом Celery
с AOF. Повторная доставка считается нормальным режимом: обработчики используют idempotency key,
уникальные ограничения и tenant-scoped операции.

Система обеспечивает:

- атомарное сохранение бизнес-изменения и outbox event;
- конкурентный claim outbox через `FOR UPDATE SKIP LOCKED`;
- один активный lease на `ProcessingJob`;
- heartbeat для длительной транскрибации и генерации;
- восстановление истёкшего lease через maintenance queue;
- late acknowledgement и возврат сообщения при аварийной остановке worker;
- ограниченное число попыток, exponential backoff и full jitter;
- dead-letter для jobs и outbox events;
- идемпотентные BBB callback, генерацию и доставку публикаций.

## Очереди

| Очередь | Назначение | Масштабирование |
|---|---|---|
| `transcription` | скачивание записи и распознавание | CPU/GPU worker, низкий prefetch |
| `materials` | генерация, TEX/HTML/PDF | CPU worker, доступ к artifact storage |
| `delivery` | публикации и уведомления | несколько лёгких workers |
| `maintenance` | outbox и восстановление lease | один или несколько workers |

Celery использует `acks_late`, `reject_on_worker_lost`, `prefetch_multiplier=1` и durable queues.
`CELERY_VISIBILITY_TIMEOUT` должен превышать hard time limit workflow. Docker даёт worker 45 секунд
для graceful shutdown.

## Жизненный цикл job

1. Worker блокирует строку job и получает lease.
2. `attempt_count` увеличивается одной транзакцией.
3. Heartbeat продлевает `lease_expires_at` каждые `JOB_LEASE_SECONDS / 3`.
4. Успех очищает lease; workflow сохраняет `completed`.
5. Ошибка очищает lease и создаёт `retrying` с bounded backoff/jitter.
6. Последняя разрешённая попытка переводит job в `dead_letter`.
7. Beat находит истёкшие lease через `SKIP LOCKED`, освобождает их и создаёт новый outbox event.

Отмена running job кооперативная. Флаг фиксируется сразу, heartbeat переводит job в `canceled`.
HTTP-вызов с синхронным клиентом завершится по настроенному timeout.

## Transactional outbox и доставка

BBB callback сохраняет receipt, recording, processing job и `post_lesson.requested` одной
транзакцией. Уникальная пара `(provider, external_event_id)` и dedup key защищают повторный callback.

Maintenance worker claim-ит доступные события. Постановка транскрибации и генерации в Redis
завершает event. Delivery event остаётся `dispatching` до успешного выполнения delivery worker.
При остановке delivery worker событие будет повторно отправлено после истечения dispatch lease.
Portal handler сохраняет доставку и уведомления идемпотентно.

## Внешние сервисы

BBB, transcription webhook, materials webhook и document engine имеют явные timeout. Circuit
breaker учитывает network error, timeout, HTTP 429 и HTTP 5xx. После порога ошибок circuit
переходит в `open`, затем допускает один recovery probe. Ошибка workflow попадает в обычную
политику bounded retry.

## Работа оператора

Преподаватель и администратор видят `/settings/tasks`. Экран показывает `failed`, `retrying`,
`canceled`, `dead_letter`, истёкшие lease и dead outbox events.

```bash
uv run tutor-assistant-ops list --organization <organization-id>
uv run tutor-assistant-ops retry <job-id> --organization <organization-id>
uv run tutor-assistant-ops cancel <job-id> --organization <organization-id>
uv run tutor-assistant-ops resend-outbox <event-id> --organization <organization-id>
uv run tutor-assistant-ops recover --limit 100
```

Каждое действие через web фиксируется в tenant-scoped audit log. Manual retry создаёт новый
outbox event, поэтому временная недоступность Redis не приводит к потере команды.

## Диагностика

```bash
docker compose ps
docker compose logs worker-transcription worker-materials worker-delivery worker-maintenance beat
uv run tutor-assistant-ops list --organization <organization-id>
uv run python -c "from tutor_assistant_web.worker import celery_app; print(celery_app.conf.task_routes)"
```

Алерты production должны учитывать:

- количество `dead_letter` jobs и dead outbox events;
- возраст старейшего pending/dispatching event;
- число истёкших lease;
- глубину каждой Redis queue;
- долю retries и открытые circuit breakers;
- длительность транскрибации, генерации и доставки.
