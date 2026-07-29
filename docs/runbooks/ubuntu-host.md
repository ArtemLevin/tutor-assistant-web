# Runbook: Ubuntu production host

Целевая платформа единого приложения — Ubuntu Server 22.04 или 24.04 LTS на
`x86_64`. Приложение, TutorBoard и GeometryOS остаются контейнерными; этот
runbook настраивает только host-уровень.

## 1. Требования к ВМ

- минимум 4 vCPU, 16 GiB RAM и 40 GiB свободного диска;
- публичный IPv4, DNS A/AAAA для домена приложения;
- вход по SSH-ключу;
- открытый SSH-порт, TCP 80/443 и UDP 443;
- отдельное внешнее S3/совместимое хранилище для backup;
- amd64-образы backend, TutorBoard и GeometryOS в GHCR.

Для рабочего production с ClamAV, PostgreSQL, blue/green deployment и
наблюдаемостью рекомендуется 8 vCPU, 32 GiB RAM и отдельный SSD от 100 GiB.

## 2. Получение deployment-контура

Под административным пользователем:

```bash
sudo git clone https://github.com/ArtemLevin/tutor-assistant-web.git \
  /opt/tutorboard-stack
cd /opt/tutorboard-stack
git switch main
```

В production разворачивается только проверенный commit/tag. После checkout
сверьте `git rev-parse HEAD` с release evidence.

## 3. Первичная настройка Ubuntu

```bash
sudo /opt/tutorboard-stack/deploy/ubuntu/bootstrap.sh \
  --deploy-user tutor-deploy \
  --install-dir /opt/tutorboard-stack \
  --ssh-port 22
```

Bootstrap:

- устанавливает Docker Engine и Compose v2 из официального Docker APT
  repository;
- включает Docker, containerd, NTP и unattended security updates;
- создаёт `tutor-deploy`, добавляет его в группу `docker`;
- проверяет наличие `authorized_keys` и отключает парольный SSH-вход;
- включает UFW для SSH, HTTP, HTTPS и HTTP/3;
- добавляет `DOCKER-USER` policy, блокирующую любые случайно опубликованные
  container ports, кроме 80/443;
- устанавливает и включает `tutorboard-stack.service`.

Если SSH работает не на 22-м порту, обязательно передайте фактический
`--ssh-port`. Bootstrap не запускает приложение до заполнения production
конфигурации.

## 4. GHCR и production secrets

Переключитесь на deployment-пользователя и войдите в GHCR:

```bash
sudo -iu tutor-deploy
cd /opt/tutorboard-stack
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u ArtemLevin --password-stdin
deploy/production/init.sh
```

Отредактируйте:

```text
deploy/production/.env.production
deploy/production/secrets/*
```

Обязательные условия:

- `PUBLIC_BASE_URL=https://APP_DOMAIN`;
- домен входит в `TRUSTED_HOSTS` и уже разрешается через DNS;
- `GEOMETRYOS_IMAGE` содержит полный `@sha256:<64 hex>`;
- provider secrets не пусты;
- `BACKUP_S3_ENDPOINT_URL` указывает на внешний HTTPS S3 endpoint;
- backup bucket и credentials отделены от artifact bucket;
- в конфигурации нет `example.com`, `REPLACE_WITH` и других placeholders.

Файлы в `secrets/` имеют режим `0600`, каталог — `0700`. Не добавляйте их в
Git, systemd unit или shell history.

## 5. Preflight и первый deployment

```bash
deploy/ubuntu/preflight.sh v1.0.0 v1.0.0
deploy/production/deploy.sh v1.0.0 v1.0.0
sudo systemctl start tutorboard-stack.service
deploy/ubuntu/host-smoke.sh --verify-backup
```

Preflight проверяет Ubuntu/version/amd64, Docker, Compose, RAM, диск, NTP, DNS,
занятость 80/443, заполнение secrets, off-host backup boundary, immutable
GeometryOS digest, доступ к каждому GHCR image и наличие `linux/amd64`
manifest.

`host-smoke.sh --verify-backup` подтверждает systemd, обязательные контейнеры,
HTTPS, security headers, TutorBoard, GeometryOS и фактическую выгрузку свежего
backup во внешнее хранилище.

## 6. systemd и диагностика

```bash
sudo systemctl status tutorboard-stack.service
sudo journalctl -u tutorboard-stack.service -n 200 --no-pager
sudo systemctl restart tutorboard-stack.service
deploy/ubuntu/stack-control.sh status
```

Compose-контейнеры используют `restart: unless-stopped`, а systemd
восстанавливает полный active slot после перезагрузки host. Остановка service
удаляет контейнеры и сети, но сохраняет named volumes.

Контейнерные логи ограничены по размеру и числу файлов. Текущие ограничения:

```bash
docker inspect tutor-production-web-blue \
  --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}'
docker system df
df -h
```

## 7. Обязательный reboot drill

После первого production deployment и после изменений systemd/firewall:

```bash
sudo reboot
```

После восстановления SSH:

```bash
cd /opt/tutorboard-stack
systemctl is-active tutorboard-stack.service
deploy/ubuntu/host-smoke.sh --verify-backup
sudo journalctl -u tutorboard-stack.service -b --no-pager
```

Release evidence должно содержать время перезагрузки, время достижения
`/health/ready`, backup ID и результат smoke. Если systemd не поднял стек,
сначала соберите journal и `docker ps -a`; не удаляйте volumes и не выполняйте
reset.

## 8. Обновление

```bash
cd /opt/tutorboard-stack
git fetch --tags origin
git switch --detach <approved-commit-or-tag>
deploy/ubuntu/preflight.sh <backend-tag> <tutorboard-tag>
deploy/production/deploy.sh <backend-tag> <tutorboard-tag>
deploy/ubuntu/host-smoke.sh
```

Rollback и restore выполняются по отдельным runbooks. Обновление Docker/Ubuntu
сначала проверяется на staging и завершается reboot drill.

Self-hosted production runner должен работать от `tutor-deploy` и выполнять
deployment из этого же канонического checkout. Workflow обновляет
`/opt/tutorboard-stack` до точного `GITHUB_SHA`; отдельный временный checkout
для production не используется, чтобы systemd и deployment state всегда
указывали на один каталог.
