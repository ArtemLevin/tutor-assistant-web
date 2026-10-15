# Единое локальное приложение

`compose.local.yml` собирает три независимых репозитория в один пользовательский
контур. Git-истории и релизные циклы остаются раздельными:

- `tutor-assistant-web` владеет пользователями, занятиями, правами, хранением,
  WebSocket collaboration и Lesson Evidence;
- TutorBoard обслуживается на `/board/`, `/boards` и `/b/<board-id>`;
  последние два пути используются для сопряжения с настольным Tutor Assistant;
- GeometryOS доступен браузеру только через аутентифицированный
  `/api/v1/geometryos/` gateway backend;
- Caddy публикует единственный локальный адрес.

## Раскладка каталогов

По умолчанию репозитории должны быть соседними:

```text
workspace/
├── tutor-assistant-web/
├── tutorboard/
└── geometryos/
```

Пути можно переопределить в `deploy/local/.env.local` через
`TUTORBOARD_CONTEXT` и `GEOMETRYOS_CONTEXT`. Допускаются абсолютные пути
Docker Desktop.

## Запуск на Windows

Требования: Docker Desktop с Compose v2 и запущенный Linux container engine.

```powershell
cd C:\путь\к\tutor-assistant-web
.\deploy\local\start-local.ps1
```

При первом запуске скрипт:

1. создаёт `deploy/local/.env.local` из безопасного локального примера;
2. проверяет соседние build-контексты и Compose;
3. собирает backend, TutorBoard и GeometryOS;
4. применяет Alembic migrations отдельным one-shot job;
5. поднимает PostgreSQL, Redis, MinIO, ClamAV, worker и scheduler;
6. запускает сквозной smoke:
   `login → lesson → board → WebSocket → GeometryOS → snapshot → evidence → publish`.

После успешной проверки:

```text
Приложение: http://localhost:8080/
TutorBoard:  http://localhost:8080/board/
Логин:       admin@localhost
Пароль:      local-demo-password
```

Значения берутся из `.env.local`; их можно изменить до первого запуска.
Изменение bootstrap-пароля не меняет пароль уже созданного администратора.

Полезные варианты:

```powershell
# Не пересобирать неизменившиеся образы
.\deploy\local\start-local.ps1 -SkipBuild

# Не выполнять сквозную проверку
.\deploy\local\start-local.ps1 -SkipSmoke

# Остановить контейнеры, сохранив данные
.\deploy\local\stop-local.ps1

# Удалить контейнеры и локальные volumes
.\deploy\local\reset-local.ps1
```

`reset-local.ps1` требует явного подтверждения. Для автоматизированного полного
сброса используется `-Force`.

## Запуск Tutor Assistant с автоматическим открытием TutorBoard

Для локального занятия с одной доской понадобится **дополнительный** репозиторий
`tutor-assistant` с Windows/PySide6-приложением. Запустите локальный Docker
stack как выше, затем войдите в браузере по `http://localhost:8080/login`.

Из этой же авторизованной сессии откройте
`http://localhost:8080/api/v1/boards/context` и скопируйте
`userId` и `organizationId`. Эти значения фиксируют конкретного
преподавателя и организацию в протоколе создания доски.

На Windows, из директории `tutor-assistant`:

```powershell
uv sync --extra desktop --extra transcription --group dev
uv run python scripts\bootstrap.py
```

В созданном `config/app.yaml` установите:

```yaml
board:
  enabled: true
  base_url: http://localhost:8080
  expected_user_id: "<userId из /api/v1/boards/context>"
  expected_organization_id: "<organizationId из /api/v1/boards/context>"
```

Затем запустите `uv run tutor-assistant-gui config\app.yaml`,
выберите ученика и тему, отметьте «Открыть новую TutorBoard с записью»,
проверьте устройства и начните запись. Desktop откроет
`http://localhost:8080/boards#ta-start=...` и получит callback на loopback,
сохранив идентификатор доски в локальной карточке занятия.

Это локальный сценарий одной учётной записи: приглашать ученика
необязательно. Настольный компьютер и браузер должны работать на одной
Windows-машине, так как callback слушает `127.0.0.1`. Держите Docker
stack запущенным на протяжении занятия. Экспорт PDF доски и совместная
публикация с транскриптом пока не реализованы.

## Ручная проверка

После входа откройте демонстрационное занятие и нажмите «Открыть TutorBoard».
Проверьте:

- рисование, выделение, undo/redo и обновление страницы;
- второй браузер или приватное окно — presence и удалённый курсор;
- временное отключение сети и последующий reconnect;
- запрос «Постройте треугольник ABC и проведите высоту AH»;
- создание итога занятия и публикацию Lesson Evidence.

Только Caddy имеет host port. PostgreSQL, Redis, MinIO, ClamAV и GeometryOS
остаются во внутренних Docker-сетях.

## Production boundary

`compose.production.yml` также содержит внутренний GeometryOS, но принимает
только полный `GEOMETRYOS_IMAGE` через окружение. Перед deployment значение
обязано иметь вид:

```text
ghcr.io/artemlevin/geometryos:v0.3.0@sha256:<64-hex-digest>
```

Deployment отказывается работать с `latest`, одним mutable-тегом или неполным
digest. Миграции по-прежнему выполняются отдельным job до переключения
blue/green-трафика.
