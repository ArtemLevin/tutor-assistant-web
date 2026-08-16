from tutor_assistant_web.modules.boards.models import BoardInvitation


def test_board_invitation_orm_indexes_match_migration_contract() -> None:
    index_names = {index.name for index in BoardInvitation.__table__.indexes}
    assert index_names == {
        "ix_board_invitations_expires",
        "ix_board_invitations_org_board_created",
    }
    assert "ix_board_invitations_organization_id" not in index_names
    assert "ix_board_invitations_board_document_id" not in index_names
