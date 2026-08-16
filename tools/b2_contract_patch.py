from pathlib import Path

path = Path("contracts/standalone-board/openapi.yaml")
text = path.read_text(encoding="utf-8")
old = '''  /api/v1/boards/context:
    get:
      tags: [guest-access]
      summary: Resolve the effective teacher or guest principal before sync starts.
      description: >-
        Teacher authentication wins when teacher and guest cookies coexist.
        Guest callers are board-scoped.
      responses:
'''
new = '''  /api/v1/boards/context:
    get:
      tags: [guest-access]
      summary: Resolve the effective teacher or guest principal before sync starts.
      description: >-
        Teacher authentication wins when teacher and guest cookies coexist.
        Guest callers are board-scoped. Standalone teacher callers MUST supply
        boardId so the server can return a strict board-scoped context. Omitting
        boardId is reserved for the temporary legacy authenticated-context bridge
        and is outside this standalone contract.
      parameters:
        - name: boardId
          in: query
          required: false
          schema: { type: string, minLength: 1, maxLength: 128 }
          description: >-
            Required for standalone teacher callers. Guest sessions are already
            scoped to exactly one board and may omit it.
      responses:
'''
if old not in text:
    raise SystemExit("standalone context contract anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
