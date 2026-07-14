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

Откройте <http://localhost:8000>. Код входа берётся из `APP_ACCESS_TOKEN` в `.env`. Если переменная
пуста, авторизация отключена для локальной разработки.

Demo-данные создаются один раз при пустой базе. Для чистой базы установите `SEED_DEMO_DATA=false`.

## Команды

```bash
make sync       # зависимости
make run        # web-приложение
make worker     # Celery worker
make check      # Ruff + pytest
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
PUBLIC_BASE_URL=https://tutor.example.com
APP_ENV=production
APP_SECRET_KEY=another-long-random-secret
APP_ACCESS_TOKEN=private-tutor-access-code
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

Compose запускает приложение, Celery worker, PostgreSQL и Redis. Сам BigBlueButton в Compose не
включён и подключается как внешний сервис.

Для локального запуска без Redis оставьте `TASK_EAGER=true`: обработка выполнится в процессе web.
Для Compose и production используется `TASK_EAGER=false`.

## Материалы после занятия

Кнопка «Сформировать» создаёт `ProcessingJob`. Worker:

1. запрашивает готовые записи BBB;
2. формирует версионированный evidence JSON;
3. отправляет его в `MATERIALS_WEBHOOK_URL`;
4. сохраняет возвращённые артефакты;
5. показывает прогресс и ошибки в карточке занятия.

Если webhook не указан, создаются два локальных черновика: итог занятия и домашнее задание.
Полный контракт описан в [docs/materials-webhook.md](docs/materials-webhook.md).

Запись BigBlueButton появляется после серверной постобработки, иногда через несколько минут после
окончания встречи. Для production следующей задачей является подключение recording-ready webhook,
который автоматически запустит pipeline в нужный момент.

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
│   ├── students/          # CRM учеников
│   ├── scheduling/        # расписание
│   ├── classroom/         # занятие и записи
│   ├── materials/         # evidence и артефакты
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
`ConferenceProvider`, `MaterialGenerator` и `JobDispatcher`.

Для ограниченного запуска перечислите корневые модули:

```dotenv
ENABLED_MODULES=students,scheduling
```

Зависимости подключаются автоматически. Пустое значение включает весь встроенный набор.

Архитектурные границы и модель доверия описаны в
[docs/architecture.md](docs/architecture.md).

## Ограничения пилота

- единый преподаватель и общий код доступа;
- схема создаётся через SQLAlchemy `create_all`, миграции Alembic пока отсутствуют;
- нет повторяющихся событий и интеграции с внешними календарями;
- нет платежей, кабинета родителя и публикации материалов ученику;
- транскрипция ожидается от BBB captions или будущего отдельного worker;
- локальные AI-материалы являются шаблонными черновиками;
- demo-доска служит только для знакомства с интерфейсом и не синхронизируется;
- журнал аудита и политика удаления персональных данных относятся к production-этапу.

Пилот предназначен для проверки реального сценария на нескольких занятиях. Перед работой с
персональными данными несовершеннолетних требуется определить согласия, сроки хранения, резервное
копирование и правила доступа.

## Ближайший production backlog

1. Пользователи, роли и tenant-изоляция.
2. Alembic и управляемые миграции.
3. Recording-ready webhook и автоматический retry записей.
4. Отдельная транскрибация `faster-whisper` с сегментами и говорящими.
5. Интеграция `latex-for-everyone` для TEX/PDF/HTML.
6. Проверка и публикация материалов в кабинете ученика.
7. Повторяющееся расписание, уведомления и iCal.
8. Наблюдаемость, аудит, backup/restore и политика retention.
