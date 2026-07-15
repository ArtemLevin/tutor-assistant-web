# SLO и error budget v1.0

Окно оценки — скользящие 30 дней. Плановые работы учитываются как недоступность: это сохраняет честность показателей. Отчёт формируется еженедельно, бюджет пересматривается ежемесячно.

| SLI | SLO | Допустимый бюджет |
|---|---:|---:|
| Успешные HTTP-запросы web | 99,5% | 0,5%, или 3 ч 36 мин недоступности за 30 дней |
| p95 обычных HTTP-запросов | ≤ 500 мс | не более 5% запросов выше порога |
| Потерянные durable jobs | 0 | бюджета нет; любое событие — P1 |
| Успешная доставка опубликованных материалов | ≥ 99,9% | 0,1% попыток |
| RPO PostgreSQL и S3 | ≤ 24 ч | последний успешный backup не старше 24 ч |
| RTO полного сервиса | ≤ 4 ч | restore drill и переключение укладываются в 4 ч |

HTTP SLI исключает `/health/*`, `/metrics` и ожидаемые 4xx. Доставка считается успешной только после идемпотентного подтверждения delivery worker. Повторённая, но успешно доставленная публикация считается успешной; dead-letter — неуспех.

Основные PromQL:

```promql
1 - sum(rate(tutor_http_requests_total{route!~"/health/.*|/metrics",status=~"5.."}[30d]))
    / clamp_min(sum(rate(tutor_http_requests_total{route!~"/health/.*|/metrics",status!~"4.."}[30d])), 0.001)

histogram_quantile(0.95,
  sum by (le) (rate(tutor_http_request_duration_seconds_bucket{route!~"/health/.*|/metrics"}[10m])))

sum(rate(tutor_workflow_duration_seconds_count{stage="delivery",outcome="success"}[30d]))
  / clamp_min(sum(rate(tutor_workflow_duration_seconds_count{stage="delivery"}[30d])), 0.001)
```

Если израсходовано 50% месячного бюджета, разрешены только исправления надёжности и уже принятые обязательства. При 100% замораживаются feature-релизы до закрытия RCA и восстановления устойчивого окна.
