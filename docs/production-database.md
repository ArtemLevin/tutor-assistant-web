# Production PostgreSQL 0.8

## Гарантии

В production приложение принимает только URL вида `postgresql+psycopg://...` и требует
`AUTO_MIGRATE=false`. Миграции выполняются отдельным одноразовым процессом до запуска web и
workers. Такой порядок исключает одновременный запуск Alembic несколькими экземплярами.

Ревизия `0007_production_postgres` добавляет:

- составные tenant foreign keys для приглашений, доступов, доставок и уведомлений;
- ограничения ролей и состояний;
- индексы расписания, кабинета, уведомлений и transactional outbox;
- уникальные пары `organization_id + id`, на которые опираются tenant foreign keys.

Outbox получает события через `FOR UPDATE SKIP LOCKED`. Несколько процессов могут безопасно
забирать разные сообщения одной очереди.

## Конфигурация

```dotenv
APP_ENV=production
DATABASE_URL=postgresql+psycopg://tutor:strong-password@postgres:5432/tutor
AUTO_MIGRATE=false
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT=30
DATABASE_POOL_RECYCLE=1800
DATABASE_STATEMENT_TIMEOUT_MS=30000
DATABASE_LOCK_TIMEOUT_MS=5000
```

Ориентир для общего лимита соединений:

```text
(web processes + worker processes) × (pool size + max overflow)
```

Полученное значение должно оставаться ниже `max_connections` PostgreSQL с резервом для
миграций, backup и административного доступа. Начальные значения рассчитаны на один web-процесс
и один worker. При горизонтальном масштабировании pool уменьшают.

## Чистое развёртывание

```bash
uv sync --extra dev
uv run alembic upgrade head
uv run tutor-assistant-web
```

Docker Compose выполняет `alembic upgrade head` сервисом `migrate`. `app`, `worker` и `beat`
ожидают его успешного завершения.

## Обновление существующего PostgreSQL

1. Перевести приложение в режим обслуживания и остановить workers.
2. Создать backup базы и каталога артефактов.
3. Проверить текущую ревизию: `uv run alembic current`.
4. Выполнить `uv run alembic upgrade head`.
5. Запустить smoke tests и затем workers.

Ревизия 0007 создаёт индексы и проверяет существующие строки при добавлении foreign keys. Для
крупной базы следует запланировать окно обслуживания. На текущем объёме пилота операции
выполняются одной транзакцией PostgreSQL.

Предварительная проверка tenant-целостности:

```sql
SELECT sa.id
FROM student_access sa
LEFT JOIN students s
  ON s.id = sa.student_id AND s.organization_id = sa.organization_id
WHERE s.id IS NULL;

SELECT md.id
FROM material_deliveries md
LEFT JOIN students s
  ON s.id = md.student_id AND s.organization_id = md.organization_id
LEFT JOIN generation_runs gr
  ON gr.id = md.generation_run_id AND gr.organization_id = md.organization_id
WHERE s.id IS NULL OR gr.id IS NULL;
```

Оба запроса должны вернуть пустой результат.

## Перенос SQLite в PostgreSQL

Команда переноса предназначена для остановленного приложения и новой целевой базы. Она:

1. обновляет исходную SQLite-базу до текущей ревизии;
2. обновляет пустую PostgreSQL-базу;
3. проверяет отсутствие пользовательских данных в целевой базе;
4. копирует таблицы одной транзакцией;
5. сверяет количество строк.

Сначала создайте копию SQLite-файла:

```bash
cp data/tutor-assistant.db backups/tutor-assistant-$(date +%F-%H%M).db
```

Запуск Linux/macOS:

```bash
export SOURCE_DATABASE_URL=sqlite:///./data/tutor-assistant.db
export TARGET_DATABASE_URL=postgresql+psycopg://tutor:password@localhost:5432/tutor
uv run tutor-assistant-db-copy --confirm-empty-target
```

PowerShell:

```powershell
$env:SOURCE_DATABASE_URL = "sqlite:///./data/tutor-assistant.db"
$env:TARGET_DATABASE_URL = "postgresql+psycopg://tutor:password@localhost:5432/tutor"
uv run tutor-assistant-db-copy --confirm-empty-target
```

Целевая база должна быть выделена специально для переноса. Команда прерывается, если обнаружит
в ней прикладные строки.

## Backup и restore

Backup в custom format:

```bash
pg_dump --format=custom --no-owner --file=tutor-$(date +%F-%H%M).dump \
  --dbname="postgresql://tutor:password@postgres:5432/tutor"
```

Утилиты PostgreSQL используют обычный префикс `postgresql://`; суффикс SQLAlchemy `+psycopg`
для них удаляется.

Проверка восстановления выполняется в отдельную базу:

```bash
createdb tutor_restore_test
pg_restore --clean --if-exists --no-owner --dbname=tutor_restore_test tutor.dump
```

После восстановления:

```bash
DATABASE_URL=postgresql+psycopg://.../tutor_restore_test uv run alembic current
curl --fail http://127.0.0.1:8000/health/ready
```

Backup считается подтверждённым только после успешного тестового восстановления.

## Rollback

Перед откатом приложения сначала остановите запись данных. Откат только ревизии 0007:

```bash
uv run alembic downgrade 0006_portal_delivery
```

Откат удаляет новые constraints и индексы, сохраняя пользовательские данные. Возврат к SQLite
выполняется восстановлением файла из созданной перед переносом копии.
