import re

from fastapi.testclient import TestClient

from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def login(client: TestClient, email: str = "admin@localhost", password: str = "test-password"):
    login_page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_from(login_page.text),
            "email": email,
            "password": password,
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_student_and_lesson_happy_path(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    settings = Settings(
        app_secret_key="test-secret",
        app_access_token="",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        bbb_demo_mode=True,
        seed_demo_data=False,
        task_eager=True,
        bootstrap_admin_password="test-password",
    )
    app = create_app(settings, database)

    with TestClient(app, follow_redirects=False) as client:
        login(client)
        students_page = client.get("/students")
        assert students_page.status_code == 200
        csrf = csrf_from(students_page.text)

        created = client.post(
            "/students",
            data={
                "csrf_token": csrf,
                "full_name": "Мария Иванова",
                "grade": "10 класс",
                "subject": "Математика",
                "hourly_rate": "2000",
            },
        )
        assert created.status_code == 303
        student_url = created.headers["location"]
        student_id = student_url.rsplit("/", 1)[-1]

        schedule = client.get("/schedule?week=2026-07-13")
        csrf = csrf_from(schedule.text)
        lesson = client.post(
            "/lessons",
            data={
                "csrf_token": csrf,
                "student_id": student_id,
                "title": "Пробное занятие",
                "topic": "Функции",
                "starts_at": "2026-07-14T16:00",
                "ends_at": "2026-07-14T17:00",
                "record_enabled": "on",
            },
        )
        assert lesson.status_code == 303

        detail = client.get(lesson.headers["location"])
        assert detail.status_code == 200
        assert "Мария Иванова" in detail.text
        assert "/join/" in detail.text

        processed = client.post(
            f"{lesson.headers['location']}/process",
            data={"csrf_token": csrf_from(detail.text)},
        )
        assert processed.status_code == 303
        updated = client.get(lesson.headers["location"])
        assert "Итоги занятия: Функции" in updated.text


def test_health_reports_demo_dependencies(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'health.db'}")
    settings = Settings(
        app_secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'health.db'}",
        seed_demo_data=False,
        bootstrap_admin_password="test-password",
    )
    with TestClient(create_app(settings, database)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["bigbluebutton"] == "demo"
    assert response.json()["checks"]["materials"] == "local-template"


def test_feature_modules_can_be_enabled_with_dependencies(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'modules.db'}")
    settings = Settings(
        app_secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'modules.db'}",
        seed_demo_data=False,
        enabled_modules="students",
        bootstrap_admin_password="test-password",
    )
    app = create_app(settings, database)

    assert app.state.installed_modules == ["identity", "students"]
    with TestClient(app, follow_redirects=False) as client:
        login(client)
        assert client.get("/students").status_code == 200
        assert client.get("/schedule").status_code == 404
