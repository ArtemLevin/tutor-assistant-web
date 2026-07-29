# Единое локальное приложение

`compose.local.yml` собирает три независимых репозитория в один пользовательский
контур. Git-истории и релизные циклы остаются раздельными:

- `tutor-assistant-web` владеет пользователями, занятиями, правами, хранением,
  WebSocket collaboration и Lesson Evidence;
- TutorBoard обслуживается на `/board/`;
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
