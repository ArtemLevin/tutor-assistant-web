from pathlib import Path

path = Path("src/tutor_assistant_web/modules/boards/models.py")
text = path.read_text(encoding="utf-8")
old = '''    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    board_document_id: Mapped[str] = mapped_column(String(128), index=True)
    secret_digest: Mapped[str] = mapped_column(String(64), unique=True)
'''
new = '''    organization_id: Mapped[str] = mapped_column(String(36))
    board_document_id: Mapped[str] = mapped_column(String(128))
    secret_digest: Mapped[str] = mapped_column(String(64), unique=True)
'''
if old not in text:
    raise SystemExit("BoardInvitation index anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
