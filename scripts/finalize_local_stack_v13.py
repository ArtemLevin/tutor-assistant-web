from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label} marker missing in {path}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(
        "compose.local.yml",
        '''    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health/live"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 5s''',
        '''    healthcheck:
      test:
        - CMD-SHELL
        - >-
          wget -q -O /dev/null http://127.0.0.1:8080/health/live &&
          wget -q -O /dev/null http://127.0.0.1:8080/board/build.json
      interval: 5s
      timeout: 8s
      retries: 20
      start_period: 5s''',
        "gateway healthcheck",
    )

    replace_once(
        "deploy/local/smoke.py",
        "from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument10",
        "from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument11",
        "generated Board document DTO",
    )
    replace_once(
        "deploy/local/smoke.py",
        "    document = BoardDocument10.model_validate(snapshot[\"document\"])",
        "    document = BoardDocument11.model_validate(snapshot[\"document\"])",
        "Board document validation",
    )
    replace_once(
        "deploy/local/smoke.py",
        '''        for item in command["commands"]:
            item["actorId"] = user_id
            item["title"] = "Проверка единого локального приложения"''',
        '''        for index, item in enumerate(command["commands"]):
            item["command"]["actorId"] = user_id
            item["command"]["title"] = "Проверка единого локального приложения"
            item["order"]["baseRevisionAtCreation"] = 0
            item["order"]["lamport"] = index + 1''',
        "ordered smoke commands",
    )


if __name__ == "__main__":
    main()
