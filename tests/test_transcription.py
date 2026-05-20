"""Tests for TranscriptionService.

Strategy
--------
- The ``OpenAI`` client is injected via the constructor, so tests pass a plain
  ``MagicMock`` — no module-level patching of ``openai.OpenAI`` is needed.
- Real ``openai`` exception instances are constructed (using ``httpx`` primitives
  that are already present as a transitive dependency) so that ``isinstance``
  checks in the service are exercised faithfully.
- A tiny fake audio file is written to ``tmp_path`` so ``file_path.open("rb")``
  succeeds without touching real audio infrastructure.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.services.transcription import TranscriptionError, TranscriptionService

# ---------------------------------------------------------------------------
# Shared httpx primitives for building openai exceptions
# ---------------------------------------------------------------------------

_FAKE_REQUEST = httpx.Request(
    "POST",
    "https://api.openai.com/v1/audio/transcriptions",
)

_TRANSCRIBED_TEXT = "Hello, this is the transcribed content."


# ---------------------------------------------------------------------------
# Exception factories
# ---------------------------------------------------------------------------

def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "Rate limit exceeded.",
        response=httpx.Response(429, request=_FAKE_REQUEST),
        body=None,
    )


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=_FAKE_REQUEST)


def _api_error() -> openai.APIError:
    return openai.APIError(
        "Internal server error.",
        _FAKE_REQUEST,
        body=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_client() -> MagicMock:
    """A MagicMock that stands in for ``openai.OpenAI``."""
    return MagicMock()


@pytest.fixture()
def service(mock_client: MagicMock) -> TranscriptionService:
    """A TranscriptionService wired to the mock client."""
    return TranscriptionService(client=mock_client)


@pytest.fixture()
def audio_file(tmp_path: Path) -> Path:
    """A minimal fake audio file that passes ``open("rb")``."""
    p = tmp_path / "sample.mp3"
    p.write_bytes(b"\xff\xfb\x90\x00" * 16)  # plausible MP3 frame header bytes
    return p


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------

class TestTranscribeAudioSuccess:
    def test_returns_transcribed_text(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.return_value.text = _TRANSCRIBED_TEXT

        result = service.transcribe_audio(audio_file)

        assert result == _TRANSCRIBED_TEXT

    def test_calls_create_with_whisper_model(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.return_value.text = _TRANSCRIBED_TEXT

        service.transcribe_audio(audio_file)

        kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-1"

    def test_calls_create_with_open_file_handle(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.return_value.text = _TRANSCRIBED_TEXT

        service.transcribe_audio(audio_file)

        kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
        # The file argument must be a real binary IO object, not just the path.
        assert hasattr(kwargs["file"], "read")

    def test_create_is_called_exactly_once(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.return_value.text = _TRANSCRIBED_TEXT

        service.transcribe_audio(audio_file)

        mock_client.audio.transcriptions.create.assert_called_once()


# ---------------------------------------------------------------------------
# Error handling: OpenAI API exceptions → TranscriptionError
# ---------------------------------------------------------------------------

class TestTranscribeAudioApiErrors:
    def test_rate_limit_error_raises_transcription_error(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.side_effect = _rate_limit_error()

        with pytest.raises(TranscriptionError, match="Rate limit"):
            service.transcribe_audio(audio_file)

    def test_rate_limit_error_chains_original_cause(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        original = _rate_limit_error()
        mock_client.audio.transcriptions.create.side_effect = original

        with pytest.raises(TranscriptionError) as exc_info:
            service.transcribe_audio(audio_file)

        assert exc_info.value.__cause__ is original

    def test_connection_error_raises_transcription_error(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.side_effect = _connection_error()

        with pytest.raises(TranscriptionError, match="connect"):
            service.transcribe_audio(audio_file)

    def test_connection_error_chains_original_cause(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        original = _connection_error()
        mock_client.audio.transcriptions.create.side_effect = original

        with pytest.raises(TranscriptionError) as exc_info:
            service.transcribe_audio(audio_file)

        assert exc_info.value.__cause__ is original

    def test_generic_api_error_raises_transcription_error(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.side_effect = _api_error()

        with pytest.raises(TranscriptionError):
            service.transcribe_audio(audio_file)

    def test_generic_api_error_chains_original_cause(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        original = _api_error()
        mock_client.audio.transcriptions.create.side_effect = original

        with pytest.raises(TranscriptionError) as exc_info:
            service.transcribe_audio(audio_file)

        assert exc_info.value.__cause__ is original

    def test_api_error_message_contains_file_name(
        self,
        service: TranscriptionService,
        mock_client: MagicMock,
        audio_file: Path,
    ) -> None:
        mock_client.audio.transcriptions.create.side_effect = _api_error()

        with pytest.raises(TranscriptionError, match=audio_file.name):
            service.transcribe_audio(audio_file)


# ---------------------------------------------------------------------------
# Non-API errors are NOT swallowed
# ---------------------------------------------------------------------------

class TestTranscribeAudioNonApiErrors:
    def test_raises_file_not_found_for_missing_file(
        self,
        service: TranscriptionService,
    ) -> None:
        missing = Path("/no/such/directory/audio.mp3")

        with pytest.raises(FileNotFoundError):
            service.transcribe_audio(missing)

    def test_file_not_found_is_not_wrapped_as_transcription_error(
        self,
        service: TranscriptionService,
    ) -> None:
        missing = Path("/no/such/directory/audio.mp3")

        with pytest.raises(Exception) as exc_info:
            service.transcribe_audio(missing)

        assert not isinstance(exc_info.value, TranscriptionError)
