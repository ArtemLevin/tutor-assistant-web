from __future__ import annotations

import json
from typing import Any

import httpx

from tutor_assistant_web.shared.contracts import GeneratedArtifact


class LocalTemplateMaterialGenerator:
    name = "local-template"

    def generate(self, evidence: dict[str, Any]) -> list[GeneratedArtifact]:
        lesson = evidence["lesson"]
        student = evidence["student"]
        topic = lesson["topic"] or lesson["title"]
        notes = lesson["tutor_notes"] or "Преподаватель пока не добавил заметки."
        return [
            GeneratedArtifact(
                kind="summary",
                title=f"Итоги занятия: {topic}",
                content=(
                    f"# {topic}\n\n"
                    f"Ученик: **{student['full_name']}** "
                    f"({student['grade'] or 'класс не указан'}).\n\n"
                    f"## Заметки преподавателя\n\n{notes}\n\n"
                    "## Следующий шаг\n\n"
                    "Проверить транскрипт и дополнить итоговый материал."
                ),
            ),
            GeneratedArtifact(
                kind="homework",
                title=f"Домашнее задание: {topic}",
                content=(
                    f"# Домашнее задание\n\nТема: **{topic}**.\n\n"
                    "1. Повторить основные определения занятия.\n"
                    "2. Решить 3 задания по теме.\n"
                    "3. Отметить шаги, которые вызвали затруднение.\n\n"
                    "> Это черновик пилота. Преподавателю следует проверить задания "
                    "перед публикацией."
                ),
            ),
        ]


class WebhookMaterialGenerator:
    name = "webhook"

    def __init__(self, url: str, token: str = "", timeout: float = 60.0) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout

    def generate(self, evidence: dict[str, Any]) -> list[GeneratedArtifact]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = httpx.post(
            self.url,
            json=evidence,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        artifacts = body.get("artifacts") if isinstance(body, dict) else None
        if not isinstance(artifacts, list):
            raise ValueError("materials webhook must return an artifacts list")
        result: list[GeneratedArtifact] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            result.append(
                GeneratedArtifact(
                    kind=str(item.get("kind", "summary")),
                    title=str(item.get("title", "Материал")),
                    content=content,
                    source_url=str(item.get("source_url", "")),
                )
            )
        return result
