from __future__ import annotations

from pathlib import Path

import openai

from app.core.config import settings
from app.core.logger import logger


class TranscriptionError(RuntimeError):
    """Raised when the Whisper API call fails for any reason.

    The original OpenAI exception is always chained as ``__cause__`` so
    callers can inspect it if they need to distinguish rate-limits from
    connection errors, etc.
    """


class TranscriptionService:
    """Transcribes audio files via the OpenAI Whisper API.

    DIP note:
        The ``openai.OpenAI`` client is injected through the constructor.
        Tests pass a ``MagicMock`` directly — no module-level patching needed.
    """

    def __init__(self, client: openai.OpenAI | None = None) -> None:
        self._client: openai.OpenAI = client or openai.OpenAI(
            api_key=settings.openai_api_key
        )
        logger.info("TranscriptionService initialised.")

    def transcribe_audio(self, file_path: Path) -> str:
        """Transcribe the audio at *file_path* and return the plain text.

        Args:
            file_path: Path to the audio file on disk.

        Returns:
            The full transcription as a UTF-8 string.

        Raises:
            FileNotFoundError: if *file_path* does not exist.
            TranscriptionError: on any OpenAI API failure (rate-limit,
                connection error, or any other API-level error).
        """
        logger.info(f"Transcribing '{file_path.name}' via Whisper.")

        try:
            with file_path.open("rb") as audio_file:
                response = self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
        except openai.RateLimitError as exc:
            logger.error(
                f"Whisper rate-limit reached for '{file_path.name}': {exc}"
            )
            raise TranscriptionError(
                f"Rate limit exceeded while transcribing '{file_path.name}'."
            ) from exc
        except openai.APIConnectionError as exc:
            logger.error(
                f"Whisper connection error for '{file_path.name}': {exc}"
            )
            raise TranscriptionError(
                f"Could not connect to the OpenAI API while transcribing "
                f"'{file_path.name}'."
            ) from exc
        except openai.APIError as exc:
            logger.error(
                f"Whisper API error for '{file_path.name}': {exc}"
            )
            raise TranscriptionError(
                f"OpenAI API error while transcribing '{file_path.name}': {exc}"
            ) from exc

        text: str = response.text
        logger.info(
            f"Transcription complete: '{file_path.name}' → {len(text)} chars."
        )
        return text
