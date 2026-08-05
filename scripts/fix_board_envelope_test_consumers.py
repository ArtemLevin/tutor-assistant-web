from __future__ import annotations

from pathlib import Path
import re

ROOTS = (Path("src"), Path("tests"))
OLD_IMPORT = re.compile(
    r"from tutor_assistant_web\.shared\.board_contracts\.board_command_envelope_schema import \(\n"
    r"\s*BoardCommandEnvelope10,\n"
    r"\)\n"
)
NEW_IMPORT = (
    "from tutor_assistant_web.modules.boards.contracts import "
    "BoardCommandEnvelopeInput\n"
)
VENDORED_COMPATIBILITY = """# Board command compatibility

| Capability | Command kind | TutorBoard reader | Server reader |
| --- | --- | --- | --- |
| Base board editing | Existing `core.*` command set | 0.1.0+ | board/v1 |
| Atomic Smart Ink acceptance | `core.objects.replace` | This release+ | board/v1 with replace support |

`core.objects.replace` carries complete original and replacement snapshots.
Older strict readers reject this command explicitly. Deployments using server
sync must update the board/v1 reader before enabling Smart Ink for shared
boards.
"""


def migrate(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "BoardCommandEnvelope10" not in text:
        return False

    updated, import_count = OLD_IMPORT.subn(NEW_IMPORT, text)
    if import_count != 1:
        raise SystemExit(f"expected one legacy import in {path}, got {import_count}")

    updated = updated.replace(" -> BoardCommandEnvelope10:", ":")
    updated = re.sub(
        r"BoardCommandEnvelope10\.model_validate\(([^\n()]*)\)",
        r"BoardCommandEnvelopeInput.model_validate(\1).root",
        updated,
    )
    if "BoardCommandEnvelope10" in updated:
        raise SystemExit(f"unmigrated legacy symbol in {path}")

    path.write_text(updated, encoding="utf-8")
    return True


def normalize_persistence_fixture() -> None:
    path = Path("tests/test_board_persistence.py")
    text = path.read_text(encoding="utf-8")
    marker = '''    payload.update(
        {
            "baseRevision": 0,
            "idempotencyKey": "client:test:batch-01",
            **changes,
        }
    )
    return BoardCommandEnvelopeInput.model_validate(payload).root'''
    replacement = '''    payload.update(
        {
            "baseRevision": 0,
            "idempotencyKey": "client:test:batch-01",
            **changes,
        }
    )
    for index, item in enumerate(payload["commands"]):
        item["order"]["baseRevisionAtCreation"] = payload["baseRevision"]
        item["order"]["lamport"] = payload["baseRevision"] * len(payload["commands"]) + index + 1
    return BoardCommandEnvelopeInput.model_validate(payload).root'''
    if marker not in text:
        raise SystemExit("persistence fixture marker missing")
    path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")


def normalize_postgres_fixture() -> None:
    path = Path("tests/test_postgres_integration.py")
    text = path.read_text(encoding="utf-8")
    fixture_marker = '''    fixture = json.loads((BOARD_FIXTURES / "board-command-envelope.json").read_text())
    fixture["baseRevision"] = 0
    barrier = threading.Barrier(2)'''
    fixture_replacement = '''    fixture = json.loads((BOARD_FIXTURES / "board-command-envelope.json").read_text())
    fixture["baseRevision"] = 0
    for index, item in enumerate(fixture["commands"]):
        item["order"]["baseRevisionAtCreation"] = 0
        item["order"]["lamport"] = index + 1
    barrier = threading.Barrier(2)'''
    if fixture_marker not in text:
        raise SystemExit("PostgreSQL fixture marker missing")
    text = text.replace(fixture_marker, fixture_replacement, 1)

    duplicate_marker = '''    duplicate = dict(fixture)
    duplicate["baseRevision"] = 1
    duplicate["idempotencyKey"] = "client:duplicate"
    envelope = BoardCommandEnvelopeInput.model_validate(duplicate).root'''
    duplicate_replacement = '''    duplicate = json.loads(json.dumps(fixture))
    duplicate["baseRevision"] = 1
    duplicate["idempotencyKey"] = "client:duplicate"
    for index, item in enumerate(duplicate["commands"]):
        item["order"]["baseRevisionAtCreation"] = 1
        item["order"]["lamport"] = len(duplicate["commands"]) + index + 1
    envelope = BoardCommandEnvelopeInput.model_validate(duplicate).root'''
    if duplicate_marker not in text:
        raise SystemExit("PostgreSQL duplicate fixture marker missing")
    path.write_text(text.replace(duplicate_marker, duplicate_replacement, 1), encoding="utf-8")


def restore_contract_provenance() -> None:
    compatibility = Path("schemas/board/v1/COMPATIBILITY.md")
    expanded = compatibility.read_text(encoding="utf-8")
    docs = Path("docs/board-envelope-v13-rollout.md")
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(expanded, encoding="utf-8")
    compatibility.write_text(VENDORED_COMPATIBILITY, encoding="utf-8")


def main() -> None:
    migrated: list[Path] = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if migrate(path):
                migrated.append(path)

    if not migrated:
        raise SystemExit("no legacy Board envelope consumers found")

    normalize_persistence_fixture()
    normalize_postgres_fixture()
    restore_contract_provenance()

    remaining = [
        path
        for root in ROOTS
        for path in root.rglob("*.py")
        if "BoardCommandEnvelope10" in path.read_text(encoding="utf-8")
    ]
    if remaining:
        raise SystemExit(f"legacy Board envelope consumers remain: {remaining}")

    print("migrated:", ", ".join(str(path) for path in migrated))


if __name__ == "__main__":
    main()
