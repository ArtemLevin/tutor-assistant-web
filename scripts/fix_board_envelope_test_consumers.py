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


def main() -> None:
    migrated: list[Path] = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if migrate(path):
                migrated.append(path)

    if not migrated:
        raise SystemExit("no legacy Board envelope consumers found")

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
