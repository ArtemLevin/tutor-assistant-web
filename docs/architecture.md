# Архитектура модульного пилота

## Принцип

Полный профиль развивается как модульный монолит: FastAPI и PostgreSQL
сохраняются, а бизнес-функции имеют явные границы. Дополнительно существует
строгий `APP_PROFILE=board` с отдельным composition root, route allowlist и
deployment-контуром. Внешние системы подключаются через provider-контракты.

```mermaid
flowchart TB
    App[Composition root] --> Registry[Module registry]
    Registry --> Students[Students]
    Registry --> Audit[Audit]
    Registry --> Schedule[Scheduling]
    Registry --> Classroom[Classroom]
    Registry --> Materials[Materials]
    Registry --> Automation[Automation]
    Registry --> Portal[Portal]
    Classroom --> Conference[ConferenceProvider]
    Materials --> Generator[MaterialGenerator]
    Materials --> Documents[DocumentEngine]
    Materials --> Storage[ArtifactStorage]
    Materials --> Jobs[JobDispatcher]
    Automation --> Speech[TranscriptionProvider]
    Automation --> Jobs
```

## Модули

| Модуль       | Ответственность                                                                        | Зависимости               |
| ------------ | -------------------------------------------------------------------------------------- | ------------------------- |
| `audit`      | неизменяемый журнал действий организации                                               | —                         |
| `identity`   | организации, пользователи, роли, сессии, CSRF                                          | audit                     |
| `students`   | профиль и контакты ученика                                                             | identity                  |
| `scheduling` | недельная сетка и конфликты                                                            | students                  |
| `classroom`  | комната, роли, записи, заметки                                                         | scheduling                |
| `materials`  | evidence, jobs и артефакты                                                             | classroom                 |
| `automation` | BBB callback, outbox, транскрипт, post-lesson workflow                                 | materials                 |
| `portal`     | связи получателей, доставки, уведомления и кабинеты                                    | automation                |
| `dashboard`  | сводка и диагностика                                                                   | materials                 |
| `boards`     | lesson-bound и standalone documents, revisions, collaboration, guest access и evidence | scheduling в full profile |

`ModuleRegistry` проверяет уникальность имён, отсутствующие зависимости и циклы. Выбор корневых
модулей задаётся `ENABLED_MODULES`; транзитивные зависимости устанавливаются автоматически.
`APP_PROFILE=board` намеренно не использует этот механизм: он собирает только
identity, audit, standalone boards и health через `build_board_container()` и
отклоняет непустой `ENABLED_MODULES`.

## Provider-контракты

- `ConferenceProvider`: `demo` или `bigbluebutton`;
- `MaterialGenerator`: локальный шаблон или HTTP webhook;
- `TranscriptionProvider`: demo, локальный faster-whisper или HTTP webhook;
- `JobDispatcher`: inline для разработки или Celery для production.
- `DocumentEngine`: локальный preview или HTTP API `latex-for-everyone`;
- `ArtifactStorage`: локальный каталог для development либо private S3/MinIO с
  проверкой размера, MIME, SHA-256, retention и опциональным ClamAV.

Application-слой зависит от протоколов из `shared/contracts.py`. Конкретные SDK и HTTP-клиенты
остаются в `providers/`. Замена BBB или генератора не требует правок бизнес-модулей.

## Поток занятия

```mermaid
sequenceDiagram
    participant BBB as BigBlueButton
    participant A as Automation
    participant O as Outbox
    participant W as Worker
    participant AI as Providers

    BBB->>A: signed recording-ready JWT
    A->>O: receipt + job + event (transaction)
    O->>W: enqueue job
    W->>BBB: getRecordings
    W->>AI: transcribe + generate + compile
    W->>A: transcript + versioned artifacts + review status
```

## Правила зависимостей

1. HTTP routes вызывают application-сервисы.
2. Routes не импортируют SQLAlchemy и BigBlueButton adapter.
3. Бизнес-модели размещаются в модуле-владельце.
4. Провайдеры реализуют общие Protocol-контракты.
5. `app.py` содержит только создание приложения и команду запуска.
6. Старые `models.py` и `services.py` служат временным compatibility facade.

Эти правила проверяются в `tests/test_architecture.py`.

## Граница организации

После успешного входа подписанная сессия содержит `user_id`, `organization_id` и роль membership.
Каждый HTTP route создаёт application-сервис в scope текущей организации. Запросы учеников,
занятий, записей, фоновых заданий и материалов всегда содержат фильтр `organization_id`.

```mermaid
flowchart LR
    Session[Signed session] --> Principal[Principal + organization_id]
    Principal --> Route[HTTP route]
    Route --> Service[Tenant-scoped service]
    Service --> Data[(Rows with organization_id)]
    Job[Celery job_id] --> Resolve[Resolve organization_id]
    Resolve --> Service
```

Публичная ссылка ученика остаётся вне пользовательской сессии. HMAC привязывает её к конкретным
`lesson_id` и `student_id`; поиск выполняется только для этой пары. Роли `admin` и `tutor` имеют
доступ к административным маршрутам. Роли `student` и `parent` получают доступ к кабинетам
только через активный `StudentAccess`.

При переключении workspace backend ищет активный membership по паре `user_id + organization_id`.
Значение из формы становится частью сессии только после этой проверки. Приглашения используют
случайный token; база хранит SHA-256, срок действия и состояния accepted/revoked.

Audit events всегда содержат `organization_id`, автора, действие, тип и идентификатор сущности.
Payload ограничивается операционными метаданными; пароли, токены и содержимое заметок в него не
попадают.

## Миграции

Alembic является владельцем схемы. Ревизия `0001_pilot` описывает схему версии 0.2,
`0002_identity_tenancy` добавляет identity и tenant-ключи, `0003_workspace_admin` — приглашения и
аудит, `0004_post_lesson_automation` — webhook receipts, outbox, транскрипты и состояние workflow,
`0005_materials_factory` — evidence bundles, generation runs, artifact versions и build logs,
`0006_portal_delivery` — recipient access, deliveries и notifications,
`0007_production_postgres` — tenant foreign keys, status constraints и составные индексы,
`0008_durable_workers`–`0010_security_observability` — durable jobs, production artifacts,
security и telemetry, `0011_board_persistence`–`0014_board_command_origins` — board journal,
collaboration/evidence и command ordering, `0015_standalone_boards`–`0016_board_guest_invites` —
standalone ownership и guest invitations.
При первом запуске версии 0.3+ база,
ранее созданная через `create_all`, автоматически получает stamp `0001_pilot`; все существующие
строки переносятся в организацию по умолчанию. Новая база проходит всю цепочку
Alembic-ревизий с нуля.

## Контейнеры

```mermaid
flowchart TB
    Browser[Браузер] --> App[FastAPI modules]
    Browser --> BBB[BigBlueButton]
    App --> Postgres[(PostgreSQL)]
    App --> Redis[(Redis)]
    Redis --> Worker[Celery worker]
    Beat[Celery Beat] --> Redis
    Worker --> BBB
    Worker --> AI[Materials provider]
    Worker --> Latex[latex-for-everyone]
    Worker --> Files[(Private S3 / MinIO)]
    App --> Files
    Worker --> Postgres
```

BigBlueButton работает отдельно. Shared secret остаётся на backend.

В production Alembic запускается отдельным migration job. Web и workers используют PostgreSQL
через настраиваемый connection pool. SQLite сохраняется как локальный development-профиль.

## Следующие архитектурные задачи

1. Внешние каналы уведомлений и пользовательские предпочтения.
2. Централизованный secrets manager и автоматическая ротация.
3. Удаление записей, retention policy и экспорт audit events.
4. Регулярный off-host DR drill, включающий observability state.
