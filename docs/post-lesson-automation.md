# Автоматическая обработка завершённого занятия

Версия 0.5 запускает конвейер после того, как BigBlueButton завершил серверную обработку записи.

## Последовательность

1. При создании комнаты приложение передаёт BBB метапараметр
   `meta_bbb-recording-ready-url`.
2. BBB отправляет `application/x-www-form-urlencoded` POST с полем `signed_parameters`.
3. Приложение проверяет JWT алгоритмом HS256 и общим `BBB_SECRET`.
4. В одной транзакции создаются receipt, `ProcessingJob`, запись аудита и outbox event.
5. Celery Beat периодически вызывает `tutor.dispatch_outbox`; событие передаётся worker.
6. Worker синхронизирует `getRecordings`, ищет прямой audio/video URL, транскрибирует запись и
   передаёт транскрипт в evidence JSON генератора материалов.

Повторный callback с тем же `record_id` возвращает успешный ответ и не создаёт вторую задачу.
Outbox возвращает зависшие события в очередь через пять минут и применяет экспоненциальную
задержку при ошибке доставки. Сам workflow также повторяется с настраиваемой задержкой.

## Локальная транскрибация

```bash
uv sync --extra transcription
```

```dotenv
TRANSCRIPTION_PROVIDER=faster-whisper
TRANSCRIPTION_MODEL=small
TRANSCRIPTION_LANGUAGE=ru
TRANSCRIPTION_DEVICE=cpu
TRANSCRIPTION_COMPUTE_TYPE=int8
```

Модель загружается при первом запуске worker. Для Docker задайте
`INSTALL_TRANSCRIPTION=true` перед сборкой worker. Каталог модели следует вынести в постоянный
cache volume при production-развёртывании.

BBB может публиковать презентационный HTML-плеер без прямого медиафайла. Конвейер принимает
форматы `podcast`, `audio`, `video` и URL с расширением mp3/wav/ogg/m4a/mp4/webm/flac. Если такой
источник ещё не появился, задача переходит в retry. На BBB следует включить подходящий playback
format либо подключить `TRANSCRIPTION_PROVIDER=webhook` к сервису, который умеет извлекать запись.

## Webhook-провайдер транскрибации

Запрос содержит `schema_version`, `record_id`, `media_url`, `metadata`. Ответ:

```json
{
  "text": "Полный текст занятия",
  "language": "ru",
  "provider": "internal-asr",
  "model": "whisper-large-v3",
  "segments": [{"start": 0.0, "end": 4.2, "text": "Начало занятия"}]
}
```

## Эксплуатация

```bash
make worker
make beat
make outbox
```

В production web, worker и beat запускаются отдельными процессами. На странице занятия видны
текущий этап, число попыток, ошибка и редактируемый транскрипт. Секрет и исходный JWT в базе и
журнале не сохраняются.
