# Runbook: rollback

## Приложение

`make production-rollback` переключает трафик на `PREVIOUS_RELEASE` без повторного запуска миграций. Операция blue/green и проходит readiness/smoke. После неё проверить jobs, доставку и корреляцию trace.

## Миграция

Схема меняется назад только если migration release содержит проверенный `downgrade()` и старое приложение несовместимо с новой схемой. Сначала создаётся backup, затем:

```bash
CONFIRM_MIGRATION_ROLLBACK=yes \
  deploy/production/rollback.sh migration <previous_revision>
```

Предпочтительная стратегия — expand/migrate/contract: новый столбец или таблица добавляются совместимо, данные мигрируют отдельно, удаление выполняется в следующем релизе. Тогда application rollback не требует schema rollback. При неуспехе остановить scheduler/workers, восстановить последний backup по restore runbook и открыть P1.
