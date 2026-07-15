# Нагрузочные и resilience-сценарии

`http.js` — обязательный gate: 100 одновременных сессий, менее 0,5% ошибок и p95 до 500 мс. `bbb-join.js` одновременно создаёт 20 реальных BBB-комнат через подписанные ссылки staging. `lesson-burst.js` проверяет workflow API тех же занятий.

```bash
k6 run -e BASE_URL=https://staging.example.com load/http.js
k6 run -e BASE_URL=https://staging.example.com \
  -e LESSON_IDS=id1,id2,... -e SESSION_COOKIE='tutor_session=...' load/lesson-burst.js
k6 run -e JOIN_URLS='https://staging.../join/...,https://staging.../join/...' load/bbb-join.js
```

Массовое завершение занятий и очередь из 100 транскрибаций создаются командой `tutor-assistant-load-fixture seed`; `wait` требует завершения всех 100 jobs без `dead_letter`. Параллельная генерация PDF/HTML проходит тем же workflow. `bbb-join.js` проверяет control plane (create/join); реальную WebRTC-нагрузку с камерой и микрофоном нужно дополнительно выполнить специализированным BBB media-load инструментом выбранной BBB-инфраструктуры.

Сбои Redis и S3, потеря egress к BBB/AI providers, а также рестарт web/workers автоматизированы `deploy/production/chaos-drill.sh`. Любой destructive-сценарий требует явного `CONFIRM_STAGING_CHAOS=yes`.
