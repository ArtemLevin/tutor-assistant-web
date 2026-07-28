from fastapi import APIRouter


def create_router(_container) -> APIRouter:
    """PR 3 installs persistence only; authenticated board endpoints arrive in PR 4."""
    return APIRouter()
