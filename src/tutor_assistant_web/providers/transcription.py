from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from tutor_assistant_web.providers.resilience import CircuitBreaker
from tutor_assistant_web.shared.contracts import (
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionSource,
)


class TranscriptionProviderError(RuntimeError):
    pass


def resolve_media_url(playback_url: str, metadata: dict[str, Any]) -> str:
    candidates: list[tuple[str, str]] = []
    formats = metadata.get("formats", []) if isinstance(metadata, dict) else []
    if isinstance(formats, list):
        for item in formats:
            if isinstance(item, dict):
                candidates.append((str(item.get("type", "")), str(item.get("url", ""))))
    candidates.append(("playback", playback_url))
    media_extensions = (".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".webm", ".flac")
    for kind, url in candidates:
        path = urlparse(url).path.lower()
        if url and (
            kind.lower() in {"podcast", "video", "audio"} or path.endswith(media_extensions)
        ):
            return url
    return ""


class DisabledTranscriptionProvider:
    name = "disabled"

    def transcribe(self, source: TranscriptionSource) -> TranscriptionResult:
        raise TranscriptionProviderError(
            "Транскрибация не настроена. Установите extra transcription и задайте "
            "TRANSCRIPTION_PROVIDER=faster-whisper либо подключите webhook."
        )


class DemoTranscriptionProvider:
    name = "demo"

    def transcribe(self, source: TranscriptionSource) -> TranscriptionResult:
        text = "Демонстрационный транскрипт занятия. Проверьте текст перед публикацией материалов."
        return TranscriptionResult(
            text=text,
            language="ru",
            segments=[TranscriptionSegment(start=0.0, end=5.0, text=text)],
            provider=self.name,
            model="demo",
        )


class WebhookTranscriptionProvider:
    name = "webhook"

    def __init__(
        self,
        url: str,
        token: str = "",
        timeout: float = 300.0,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.circuit_breaker = circuit_breaker or CircuitBreaker("transcription-webhook")

    def transcribe(self, source: TranscriptionSource) -> TranscriptionResult:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with self.circuit_breaker.guard():
            response = httpx.post(
                self.url,
                json={
                    "schema_version": "1.0",
                    "record_id": source.record_id,
                    "media_url": source.media_url,
                    "metadata": source.metadata,
                },
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise TranscriptionProviderError("transcription webhook must return a text field")
        segments = []
        for item in body.get("segments", []):
            if isinstance(item, dict):
                segments.append(
                    TranscriptionSegment(
                        start=float(item.get("start", 0)),
                        end=float(item.get("end", 0)),
                        text=str(item.get("text", "")),
                    )
                )
        return TranscriptionResult(
            text=body["text"],
            language=str(body.get("language", "")),
            segments=segments,
            provider=str(body.get("provider", self.name)),
            model=str(body.get("model", "external")),
        )


class FasterWhisperTranscriptionProvider:
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model: str,
        language: str,
        device: str,
        compute_type: str,
        timeout: float,
        max_download_mb: int,
    ) -> None:
        self.model_name = model
        self.language = language or None
        self.device = device
        self.compute_type = compute_type
        self.timeout = timeout
        self.max_bytes = max_download_mb * 1024 * 1024
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionProviderError(
                    "Для локальной транскрибации выполните uv sync --extra transcription"
                ) from exc
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def _download(self, url: str, target: Path) -> None:
        if urlparse(url).scheme not in {"http", "https"}:
            raise TranscriptionProviderError("media URL must use http or https")
        size = 0
        with httpx.stream("GET", url, timeout=self.timeout, follow_redirects=True) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise TranscriptionProviderError(
                            "recording exceeds configured download limit"
                        )
                    output.write(chunk)

    def transcribe(self, source: TranscriptionSource) -> TranscriptionResult:
        suffix = Path(urlparse(source.media_url).path).suffix or ".media"
        with tempfile.TemporaryDirectory(prefix="tutor-transcription-") as directory:
            target = Path(directory) / f"recording{suffix}"
            self._download(source.media_url, target)
            segments_iter, info = self._load_model().transcribe(
                str(target), language=self.language, vad_filter=True, beam_size=5
            )
            segments = [
                TranscriptionSegment(
                    start=float(item.start),
                    end=float(item.end),
                    text=item.text.strip(),
                )
                for item in segments_iter
            ]
        return TranscriptionResult(
            text=" ".join(item.text for item in segments).strip(),
            language=str(info.language or self.language or ""),
            segments=segments,
            provider=self.name,
            model=self.model_name,
        )


def segment_payload(segment: TranscriptionSegment) -> dict[str, Any]:
    return {"start": segment.start, "end": segment.end, "text": segment.text}
