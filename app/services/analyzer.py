from __future__ import annotations

import openai

from app.core.config import settings
from app.core.logger import logger
from app.models.schemas import CallAnalysis

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Quality Assurance manager at a Ukrainian auto service center.
Your job is to evaluate recorded phone calls between service managers and clients.

TASK
----
Analyze the transcript provided by the user and extract a structured assessment
with the following fields:

- has_recording: set to false only when the transcript is empty, silent, or
  completely unintelligible — i.e. there is no real conversation to assess.

- work_type: identify the type of auto-service work discussed (write in
  Ukrainian). If multiple services are mentioned, use the primary one.
  Use "Невідомо" if no specific service can be identified.

- manager_evaluation: write 1-3 sentences evaluating the manager's
  communication quality, accuracy of information, and professionalism.

- is_call_ok: set to false if the manager was rude, gave incorrect or
  incomplete information, ignored the client's question, failed to provide
  a price or timeframe when asked, or behaved unprofessionally in any way.

- red_flag_comment: if is_call_ok is false, write one concise sentence
  (in Ukrainian) describing the exact problem. Set to null if is_call_ok
  is true.

- score: 1 when is_call_ok is true, 0 when is_call_ok is false.

RULES
-----
- Base your assessment solely on the transcript text.
- Do not invent details that are not present in the transcript.
- Always ensure score is consistent with is_call_ok (score=1 ↔ is_call_ok=true).
- Always ensure red_flag_comment is null when is_call_ok is true.
"""

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AnalysisError(RuntimeError):
    """Raised when the GPT analysis call fails or returns an unusable result.

    The original ``openai`` exception is always chained as ``__cause__``.
    """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AnalyzerService:
    """Analyzes call transcripts via OpenAI Structured Outputs (GPT-4o-mini).

    DIP note:
        The ``openai.OpenAI`` client is constructor-injected so tests pass a
        ``MagicMock`` directly — no module-level patching required.
    """

    _MODEL = "gpt-4o-mini"

    def __init__(self, client: openai.OpenAI | None = None) -> None:
        self._client: openai.OpenAI = client or openai.OpenAI(
            api_key=settings.openai_api_key
        )
        logger.info("AnalyzerService initialised.")

    def analyze_transcript(self, transcript: str) -> CallAnalysis:
        """Analyze *transcript* and return a structured ``CallAnalysis``.

        Args:
            transcript: The full plain-text transcript of the call.

        Returns:
            A validated ``CallAnalysis`` Pydantic model instance.

        Raises:
            AnalysisError: if the API call fails, or if the model refuses
                to produce a structured response (``parsed`` is ``None``).
        """
        logger.info(
            f"Analysing transcript ({len(transcript)} chars) "
            f"with {self._MODEL}."
        )

        try:
            completion = self._client.beta.chat.completions.parse(
                model=self._MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                response_format=CallAnalysis,
            )
        except openai.RateLimitError as exc:
            logger.error(f"Rate-limit hit during analysis: {exc}")
            raise AnalysisError("Rate limit exceeded during transcript analysis.") from exc
        except openai.APIConnectionError as exc:
            logger.error(f"Connection error during analysis: {exc}")
            raise AnalysisError("Could not connect to the OpenAI API for analysis.") from exc
        except openai.APIError as exc:
            logger.error(f"API error during analysis: {exc}")
            raise AnalysisError(f"OpenAI API error during transcript analysis: {exc}") from exc

        message = completion.choices[0].message

        if message.parsed is None:
            refusal = message.refusal or "no reason given"
            logger.warning(f"Model refused to analyse the transcript: {refusal}")
            raise AnalysisError(
                f"Model refused to produce a structured analysis: {refusal}"
            )

        result: CallAnalysis = message.parsed
        logger.info(
            f"Analysis complete — work_type='{result.work_type}' "
            f"is_call_ok={result.is_call_ok} score={result.score}"
        )
        return result
