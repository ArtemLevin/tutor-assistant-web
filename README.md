# Tutor Assistant Web · BigBlueButton pilot

Быстрый пилот единого рабочего пространства репетитора: ученики, недельное расписание,
виртуальный класс BigBlueButton и фоновая подготовка материалов после занятия.

Пилот можно запустить без BigBlueButton. В `BBB_DEMO_MODE=true` доступны все административные
сценарии, подписанная ссылка ученика и демонстрационный экран класса. После подключения реального
BBB ссылки ведут в полноценную комнату с видео, аудио, записью и многопользовательской доской.

## Что уже работает

- карточки учеников: класс, предмет, цель, контакты родителя, ставка, ссылки и заметки;
- недельное расписание с локальным часовым поясом и проверкой пересечений;
- snapshot ставки в каждом занятии;
- отдельные роли преподавателя и ученика в BigBlueButton;
- случайные идентификаторы и пароли комнаты;
- HMAC-подписанная публичная ссылка ученика;
- включение записи с уведомлением о согласии участников;
- завершение комнаты преподавателем;
- синхронизация метаданных записей через `getRecordings`;
- Celery-задача обработки занятия;
- универсальный webhook генерации материалов;
- локальные Markdown-черновики, если AI webhook ещё не настроен;
- большой редактор сводных заметок;
- тёмный адаптивный интерфейс;
- health checks, Docker Compose, Makefile, uv и CI.
- модульный composition root и явный реестр функций;
- заменяемые провайдеры конференций, материалов и фоновых заданий;
- возможность включать только выбранные модули через `ENABLED_MODULES`.
- организации, пользователи и membership-роли `admin`, `tutor`, `student`, `parent`;
- Argon2-хеширование паролей и подписанные пользовательские сессии;
- обязательная tenant-изоляция CRM, расписания, комнат, jobs и материалов;
- управляемые миграции Alembic и автоматический upgrade пилотной базы без потери данных.
- управление участниками и ролями организации через административный интерфейс;
- одноразовые приглашения с ограниченным сроком действия;
- переключение между доступными рабочими пространствами;
- tenant-scoped журнал аудита административных и бизнес-операций.
- подписанный `recording-ready` callback BigBlueButton с защитой от повторной доставки;
- transactional outbox, Celery Beat и экспоненциальные повторные попытки;
- локальная транскрибация через `faster-whisper` либо заменяемый transcription webhook;
- сегменты транскрипта, редактирование текста и включение расшифровки в evidence JSON;
- наблюдаемые этапы post-lesson workflow и идемпотентное сохранение материалов.
- `LessonEvidenceBundle v1` с JSON Schema и SHA-256 снимком входных данных;
- заменяемые `DocumentEngine` и `ArtifactStorage`;
- версионированные TEX, HTML и PDF с журналом сборки;
- отдельные стадии проверки, согласования и публикации;
- адаптер компиляции к реальному API `latex-for-everyone`.
- ролевые приглашения ученика и родителя из карточки ученика;
- личный кабинет с опубликованными PDF/HTML/TEX;
- атомарная публикация и отзыв через transactional outbox;
- tenant-scoped доставки и внутренние уведомления.

## Быстрый старт в demo-режиме

Требования: Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --extra dev
uv run tutor-assistant-web
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --extra dev
uv run tutor-assistant-web
```

Откройте <http://localhost:8000>. Используйте `BOOTSTRAP_ADMIN_EMAIL` и
`BOOTSTRAP_ADMIN_PASSWORD` из `.env`. Эти значения создают первого администратора при пустой базе.
Следующие запуски сохраняют существующий пароль; изменение переменной не сбрасывает учётную запись.

Demo-данные создаются один раз при пустой базе. Для чистой базы установите `SEED_DEMO_DATA=false`.

## Команды

```bash
make sync       # зависимости
make migrate    # применить миграции базы
make run        # web-приложение
make worker     # Celery worker
make beat       # планировщик transactional outbox
make outbox     # однократная отправка накопленных событий
make sync-transcription # зависимости локального faster-whisper
make check      # Ruff + pytest
make schema-check # проверить контракт LessonEvidenceBundle v1
make diagnose   # безопасная диагностика конфигурации
make docker-up  # app + worker + PostgreSQL + Redis
```

Если Windows Make сообщает, что `uv` не найден, проверьте `uv --version` в том же PowerShell и
передайте полный путь:

```powershell
make sync UV="$env:USERPROFILE\.local\bin\uv.exe"
```

Прямые команды `uv ...` остаются основным и кроссплатформенным способом запуска.

## Подключение BigBlueButton

BigBlueButton следует разместить на отдельном сервере в соответствии с его
[официальной инструкцией](https://docs.bigbluebutton.org/administration/install/). Он требует домен,
TLS, доступные WebRTC-порты и поддерживаемую Ubuntu.

На BBB-сервере получите URL и shared secret:

```bash
sudo bbb-conf --secret
```

Заполните `.env`:

```dotenv
BBB_DEMO_MODE=false
BBB_BASE_URL=https://class.example.com
BBB_SECRET=long-shared-secret
TRANSCRIPTION_PROVIDER=faster-whisper
PUBLIC_BASE_URL=https://tutor.example.com
APP_ENV=production
APP_SECRET_KEY=another-long-random-secret
BOOTSTRAP_ADMIN_EMAIL=tutor@example.com
BOOTSTRAP_ADMIN_PASSWORD=a-long-unique-admin-password
SESSION_COOKIE_SECURE=true
SEED_DEMO_DATA=false
```

Приложение использует официальный checksum API:

- `create` — идемпотентно создаёт комнату;
- `join` — выдаёт роли `MODERATOR` и `VIEWER`;
- `end` — завершает встречу;
- `getRecordings` — получает готовые записи.

Shared secret остаётся только на backend. В браузер передаются уже подписанные join URL.

## Docker Compose

Создайте `.env`, затем выполните:

```bash
docker compose up --build
```

Compose запускает приложение, Celery worker, Celery Beat, PostgreSQL и Redis. Сам BigBlueButton в
Compose не включён и подключается как внешний сервис.

Для локального запуска без Redis оставьте `TASK_EAGER=true`: обработка выполнится в процессе web.
Для Compose и production используется `TASK_EAGER=false`.

## Материалы после занятия

Кнопка «Сформировать» по-прежнему доступна для ручного перезапуска. Основной автоматический путь:

1. BBB подписанным callback сообщает о готовности записи;
2. receipt, job и outbox event сохраняются одной транзакцией;
3. worker синхронизирует прямой медиаисточник и транскрибирует его;
4. транскрипт и заметки фиксируются как `LessonEvidenceBundle v1` с SHA-256;
5. генератор создаёт содержимое, `DocumentEngine` собирает TEX/HTML/PDF;
6. преподаватель проверяет комплект, согласует и отдельно публикует его.

Если webhook не указан, создаются два локальных черновика: итог занятия и домашнее задание.
Контракты описаны в [docs/materials-webhook.md](docs/materials-webhook.md) и
[docs/post-lesson-automation.md](docs/post-lesson-automation.md).
Версионирование, компиляция и review lifecycle описаны в
[docs/materials-factory.md](docs/materials-factory.md).
Кабинеты, модель доступа и безопасная доставка описаны в
[docs/portal-delivery.md](docs/portal-delivery.md).

По умолчанию включён локальный детерминированный preview-движок. Для настоящей PDF-компиляции
подключите отдельный экземпляр `latex-for-everyone`:

```dotenv
DOCUMENT_ENGINE_PROVIDER=latex-for-everyone
DOCUMENT_ENGINE_URL=https://latex.example.com
DOCUMENT_ENGINE_TOKEN=service-access-token
ARTIFACT_STORAGE_ROOT=./data/artifacts
```

Запись BigBlueButton появляется после серверной постобработки, иногда через несколько минут после
окончания встречи. Callback фиксируется сразу; отсутствие прямого audio/video URL переводит job в
управляемый retry.

Для локальной транскрибации:

```bash
uv sync --extra transcription
```

```dotenv
TRANSCRIPTION_PROVIDER=faster-whisper
TRANSCRIPTION_MODEL=small
TRANSCRIPTION_DEVICE=cpu
TRANSCRIPTION_COMPUTE_TYPE=int8
```

## Диагностика

```bash
uv run python -m pytest
uv run ruff check .
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

`/health/ready` проверяет БД и сообщает режим BBB и очереди без вывода секретов.

## Структура

```text
src/tutor_assistant_web/
├── app.py                 # минимальная точка запуска
├── bootstrap/             # composition root, DI-контейнер, реестр модулей
├── modules/
│   ├── identity/          # доступ и сессии
│   ├── audit/             # журнал действий организации
│   ├── students/          # CRM учеников
│   ├── scheduling/        # расписание
│   ├── classroom/         # занятие и записи
│   ├── automation/        # callback, outbox, транскрипт и workflow
│   ├── materials/         # evidence и артефакты
│   ├── portal/            # кабинеты, доставки и уведомления
│   └── dashboard/         # главная страница и health checks
├── providers/             # BBB/demo, webhook/local, Celery/inline
├── shared/                # контракты, ошибки, security, web helpers
├── bbb.py                 # низкоуровневый checksum API
├── models.py              # compatibility exports
├── services.py            # compatibility facade
├── worker.py              # Celery entrypoint
├── static/                # минималистичный UI
└── templates/             # server-rendered страницы
```

Каждый модуль содержит собственные модели, application-сервисы и HTTP routes. Маршруты не
обращаются к SQLAlchemy и BigBlueButton напрямую. Composition root выбирает реализации контрактов
`ConferenceProvider`, `TranscriptionProvider`, `MaterialGenerator`, `DocumentEngine`,
`ArtifactStorage` и `JobDispatcher`.

## Пользователи, роли и организации

При первом запуске создаются организация и администратор из `BOOTSTRAP_*`. Пароль хранится как
Argon2-хеш. После входа каждый application-сервис получает `organization_id` из подписанной сессии;
идентификатор из формы или query string для выбора организации не принимается.

Роли `admin` и `tutor` могут работать с текущим административным интерфейсом. Роли `student` и
`parent` входят в доменную модель и будут использованы в отдельных кабинетах. Администратор
управляет участниками в разделе «Команда»: создаёт приглашения, меняет роли и отключает доступ.
Защита не позволяет отключить собственное членство или удалить последнего администратора.

Приглашение хранится в базе в виде SHA-256 хеша токена. Исходная ссылка показывается
администратору один раз, действует `INVITATION_TTL_HOURS` часов и может быть отозвана. Пользователь
с существующим email подтверждает приглашение своим текущим паролем.

Если пользователь состоит в нескольких организациях, селектор рабочего пространства появляется
в боковой панели. Сервер проверяет membership при каждом переключении и обновляет tenant-контекст
подписанной сессии.

Для явного обновления схемы:

```bash
uv run alembic upgrade head
```

`AUTO_MIGRATE=true` выполняет эту команду при старте приложения. При обновлении с версии 0.2
существующие ученики, занятия, записи и материалы сохраняются и относятся к начальной организации.
Перед production-обновлением всё равно создайте backup базы.

Раздел «Аудит» доступен администраторам. Он фиксирует создание и принятие приглашений, изменение
membership, переключение workspace, создание и изменение учеников, создание и завершение занятий,
редактирование заметок и запуск генерации материалов.

Для ограниченного запуска перечислите корневые модули:

```dotenv
ENABLED_MODULES=students,scheduling
```

Зависимости подключаются автоматически. Пустое значение включает весь встроенный набор.

Архитектурные границы и модель доверия описаны в
[docs/architecture.md](docs/architecture.md).

## Ограничения пилота

- доставка приглашений по email пока не подключена: администратор передаёт ссылку вручную;
- нет повторяющихся событий и интеграции с внешними календарями;
- нет платежей и внешних email/Telegram-уведомлений;
- качество транскрипции зависит от выбранной Whisper-модели и качества записи;
- diarization говорящих пока не подключён;
- локальные AI-материалы являются шаблонными черновиками;
- demo-доска служит только для знакомства с интерфейсом и не синхронизируется;
- журнал аудита и политика удаления персональных данных относятся к production-этапу.

Пилот предназначен для проверки реального сценария на нескольких занятиях. Перед работой с
персональными данными несовершеннолетних требуется определить согласия, сроки хранения, резервное
копирование и правила доступа.

## Ближайший production backlog

1. S3/MinIO-адаптер `ArtifactStorage`, retention и антивирусная проверка.
2. Email/Telegram-уведомления и настройки предпочтений получателя.
3. Diarization говорящих и словарь терминов конкретного ученика.
4. Повторяющееся расписание, уведомления и iCal.
5. Метрики Prometheus/OpenTelemetry, backup/restore и disaster recovery drill.
