from __future__ import annotations

from tutor_assistant_web.bbb import BigBlueButtonClient
from tutor_assistant_web.shared.contracts import (
    ConferenceRecording,
    CreateConference,
    JoinConference,
)


class DemoConferenceProvider:
    name = "demo"
    is_demo = True

    def create_room(self, command: CreateConference) -> None:
        return None

    def join_url(self, command: JoinConference) -> str:
        return command.demo_url

    def end_room(self, meeting_id: str) -> None:
        return None

    def recordings(self, meeting_id: str) -> list[ConferenceRecording]:
        return []


class BigBlueButtonConferenceProvider:
    name = "bigbluebutton"
    is_demo = False

    def __init__(self, client: BigBlueButtonClient) -> None:
        self.client = client

    def create_room(self, command: CreateConference) -> None:
        self.client.create_meeting(
            meeting_id=command.meeting_id,
            name=command.name,
            attendee_password=command.attendee_password,
            moderator_password=command.moderator_password,
            record=command.record,
            recording_ready_url=command.recording_ready_url,
        )

    def join_url(self, command: JoinConference) -> str:
        return self.client.join_url(
            meeting_id=command.meeting_id,
            full_name=command.full_name,
            password=command.password,
            user_id=command.user_id,
            role=command.role,
            logout_url=command.logout_url,
        )

    def end_room(self, meeting_id: str) -> None:
        self.client.end_meeting(meeting_id)

    def recordings(self, meeting_id: str) -> list[ConferenceRecording]:
        return [
            ConferenceRecording(
                record_id=item.record_id,
                state=item.state,
                playback_url=item.playback_url,
                metadata=item.metadata,
            )
            for item in self.client.get_recordings(meeting_id)
        ]
