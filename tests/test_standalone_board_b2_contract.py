from pathlib import Path


def test_standalone_context_contract_documents_teacher_board_scope() -> None:
    contract = (
        Path(__file__).parents[1]
        / "contracts"
        / "standalone-board"
        / "openapi.yaml"
    ).read_text(encoding="utf-8")
    context_block = contract.split("  /api/v1/boards/context:\n", 1)[1].split(
        "  /api/v1/boards/{boardId}:\n", 1
    )[0]
    assert "name: boardId" in context_block
    assert "in: query" in context_block
    assert "Required for standalone teacher callers" in context_block
    assert "Teacher authentication wins" in context_block
