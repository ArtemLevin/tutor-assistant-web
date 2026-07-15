# Release checklist v1.0.0

- [ ] CI: format, lint, unit tests и PostgreSQL/Redis/MinIO integration tests зелёные.
- [ ] Bandit, dependency audit, secret scan, Trivy filesystem/image scan не имеют critical/high без принятого исключения.
- [ ] Migration check: upgrade → downgrade one revision → upgrade → `alembic check`.
- [ ] Все images собраны multi-stage, запускаются UID 10001 и опубликованы immutable tag/digest.
- [ ] Staging развёрнут `deploy.sh` одним pipeline, migration выполнена отдельным job.
- [ ] Smoke tests и security headers проходят через TLS endpoint.
- [ ] 100 sessions: p95 ≤ 500 мс, ошибки < 0,5%.
- [ ] 20 занятий, массовое завершение, 100 transcription jobs и PDF/HTML generation завершены без потерянных/дублированных jobs.
- [ ] Во время обработки очереди выполнены рестарт web/workers и временные сбои Redis/S3/BBB; очередь восстановилась.
- [ ] Свежий PostgreSQL+S3 backup восстановлен отдельно, SHA-256 и sample artifacts проверены, RTO < 4 ч.
- [ ] Application rollback практически переключён на previous release; migration downgrade проверен на staging snapshot.
- [ ] SLO dashboard и Alertmanager webhook проверены тестовым сигналом; on-call знает runbooks.
- [ ] Production approval получен от required reviewer.
- [ ] После production smoke создан annotated tag и GitHub Release `v1.0.0`; tag указывает на развёрнутый commit.
