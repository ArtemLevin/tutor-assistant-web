import hashlib
from urllib.parse import urlencode

from tutor_assistant_web.bbb import BigBlueButtonClient
from tutor_assistant_web.services import join_token, verify_join_token


def test_bbb_signature_uses_documented_sha1_scheme():
    client = BigBlueButtonClient("https://bbb.example", "shared-secret")
    params = {"name": "Алгебра", "meetingID": "lesson-1"}
    query = urlencode(params)
    expected = hashlib.sha1(f"create{query}shared-secret".encode()).hexdigest()

    assert client.signed_url("create", params) == (
        f"https://bbb.example/bigbluebutton/api/create?{query}&checksum={expected}"
    )


def test_join_token_is_bound_to_lesson_and_student():
    token = join_token("lesson-a", "student-a", "secret")

    assert verify_join_token("lesson-a", "student-a", token, "secret")
    assert not verify_join_token("lesson-b", "student-a", token, "secret")
    assert not verify_join_token("lesson-a", "student-b", token, "secret")
