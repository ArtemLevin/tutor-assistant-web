from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx


class BigBlueButtonError(RuntimeError):
    pass


@dataclass(frozen=True)
class Recording:
    record_id: str
    state: str
    playback_url: str
    metadata: dict[str, Any]


class BigBlueButtonClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/bigbluebutton/api"):
            self.api_url = self.base_url
        else:
            self.api_url = f"{self.base_url}/bigbluebutton/api"
        self.secret = secret
        self.timeout = timeout

    def signed_url(self, call: str, params: dict[str, Any] | None = None) -> str:
        clean = {key: value for key, value in (params or {}).items() if value is not None}
        query = urlencode(clean, doseq=True)
        checksum = hashlib.sha1(f"{call}{query}{self.secret}".encode()).hexdigest()
        separator = "&" if query else ""
        return f"{self.api_url}/{call}?{query}{separator}checksum={checksum}"

    def _call(self, call: str, params: dict[str, Any] | None = None) -> ElementTree.Element:
        try:
            response = httpx.get(self.signed_url(call, params), timeout=self.timeout)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            raise BigBlueButtonError(f"BigBlueButton request failed: {exc}") from exc
        if root.findtext("returncode") != "SUCCESS":
            message = root.findtext("message") or root.findtext("messageKey") or "unknown error"
            raise BigBlueButtonError(message)
        return root

    def create_meeting(
        self,
        *,
        meeting_id: str,
        name: str,
        attendee_password: str,
        moderator_password: str,
        record: bool,
        recording_ready_url: str = "",
    ) -> None:
        self._call(
            "create",
            {
                "name": name,
                "meetingID": meeting_id,
                "attendeePW": attendee_password,
                "moderatorPW": moderator_password,
                "record": str(record).lower(),
                "autoStartRecording": "false",
                "allowStartStopRecording": "true",
                "notifyRecordingIsOn": "true",
                "multiUserWhiteboardEnabled": "true",
                "meetingExpireIfNoUserJoinedInMinutes": 30,
                "meetingExpireWhenLastUserLeftInMinutes": 10,
                "meta_bbb-recording-ready-url": recording_ready_url or None,
            },
        )

    def join_url(
        self,
        *,
        meeting_id: str,
        full_name: str,
        password: str,
        user_id: str,
        role: str,
        logout_url: str,
    ) -> str:
        return self.signed_url(
            "join",
            {
                "fullName": full_name,
                "meetingID": meeting_id,
                "password": password,
                "userID": user_id,
                "role": role,
                "redirect": "true",
                "logoutURL": logout_url,
                "userdata-bbb_prefer_dark_theme": "true",
            },
        )

    def end_meeting(self, meeting_id: str) -> None:
        self._call("end", {"meetingID": meeting_id})

    def is_running(self, meeting_id: str) -> bool:
        root = self._call("isMeetingRunning", {"meetingID": meeting_id})
        return root.findtext("running", "false").lower() == "true"

    def get_recordings(self, meeting_id: str) -> list[Recording]:
        root = self._call("getRecordings", {"meetingID": meeting_id})
        recordings: list[Recording] = []
        for node in root.findall("./recordings/recording"):
            formats: list[dict[str, Any]] = []
            for format_node in node.findall("./playback/format"):
                formats.append(
                    {
                        "type": format_node.findtext("type", ""),
                        "url": format_node.findtext("url", ""),
                        "length": format_node.findtext("length", ""),
                    }
                )
            playback = next((item["url"] for item in formats if item["url"]), "")
            metadata = {
                "name": node.findtext("name", ""),
                "start_time": node.findtext("startTime", ""),
                "end_time": node.findtext("endTime", ""),
                "participants": node.findtext("participants", "0"),
                "published": node.findtext("published", "false"),
                "formats": formats,
            }
            recordings.append(
                Recording(
                    record_id=node.findtext("recordID", ""),
                    state=node.findtext("state", "unknown"),
                    playback_url=playback,
                    metadata=metadata,
                )
            )
        return [recording for recording in recordings if recording.record_id]
