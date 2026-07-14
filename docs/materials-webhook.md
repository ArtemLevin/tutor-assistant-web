# Контракт генератора материалов

Если `MATERIALS_WEBHOOK_URL` пуст, worker создаёт локальные демонстрационные Markdown-черновики.
При наличии URL worker отправляет `POST` с JSON-пакетом занятия.

## Запрос

```json
{
  "schema_version": "1.0",
  "organization_id": "uuid",
  "lesson": {
    "id": "uuid",
    "title": "Занятие",
    "topic": "Подобие треугольников",
    "started_at": "2026-07-14T14:00:00+00:00",
    "ended_at": "2026-07-14T15:00:00+00:00",
    "tutor_notes": "Разобрали два признака подобия"
  },
  "student": {
    "id": "uuid",
    "full_name": "Анна Смирнова",
    "grade": "9 класс",
    "subject": "Математика",
    "goal": "Подготовка к ОГЭ"
  },
  "recordings": [],
  "requested_artifacts": ["lesson_summary", "homework", "parent_report"]
}
```

При наличии `MATERIALS_WEBHOOK_TOKEN` добавляется заголовок
`Authorization: Bearer <token>`.

## Ответ

```json
{
  "artifacts": [
    {
      "kind": "summary",
      "title": "Итоги занятия",
      "content": "# Итоги...",
      "source_url": ""
    },
    {
      "kind": "homework",
      "title": "Домашнее задание",
      "content": "# Задания..."
    }
  ]
}
```

`content` может содержать Markdown, JSON или строку LaTeX. Пилот хранит результат как текст.
Компиляция PDF и публикация ученику относятся к следующему этапу и должны выполняться после
подтверждения преподавателем.
