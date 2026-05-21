from __future__ import annotations

import openai

from app.core.config import settings
from app.core.logger import logger
from app.models.schemas import CallAnalysis

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# Static body — the call-date header is prepended dynamically by
# _build_system_prompt() so every test / call that omits a date still gets
# a fully valid prompt.
_SYSTEM_PROMPT_BODY = """\
You are a Quality Assurance manager at a Ukrainian auto service center.
Your job is to evaluate recorded phone calls between service managers and clients.

TASK
----
Analyze the transcript provided by the user and populate every field below.

── OVERALL QUALITY ──────────────────────────────────────────────────────────

- has_recording: false only when the transcript is empty, silent, or completely
  unintelligible (no real conversation to assess). True otherwise.

- work_type: the primary auto-service work discussed during the call.
  Choose EXACTLY one value from the allowed list enforced by the schema —
  do NOT write synonyms, abbreviations, or free text (e.g. write
  "Компʼютерна діагностика", not "КД" or "діагностика"; write "комплексне ТО",
  not "ТО" or "технічне обслуговування").
  Use "інший варіант" when no value in the list matches the work discussed.

- manager_evaluation: 1-3 sentences evaluating the manager's communication
  quality, accuracy of information, and professionalism.

- is_call_ok: false if the manager was rude, gave incorrect or incomplete
  information, ignored the client's question, failed to provide a price or
  timeframe when asked, or behaved unprofessionally in any way.

- red_flag_comment: if is_call_ok is false — one concise sentence in Ukrainian
  describing the exact problem (e.g. "Менеджер не надав ціну на послугу").
  Must be null when is_call_ok is true.

- score: 1 when is_call_ok is true, 0 when is_call_ok is false.

── SCRIPT CHECKLIST (binary: 1 = fulfilled, 0 = not fulfilled) ─────────────

- greeting_start: 1 if the manager greeted the client and introduced
  themselves / the company at the very start of the call; 0 otherwise.

- asked_car_body: 1 if the manager asked about the car body type or model
  variant (кузов, e.g. sedan, SUV, hatchback, універсал); 0 otherwise.

- asked_car_year: 1 if the manager asked about the car's year of manufacture
  (рік автомобіля); 0 otherwise.

- asked_mileage: 1 if the manager asked about the car's current mileage
  (пробіг); 0 otherwise.

- offered_diagnostics: 1 if the manager offered or suggested comprehensive /
  computer diagnostics (комплексна діагностика); 0 otherwise.

- asked_previous_works: 1 if the manager asked what work had previously been
  done on the car (які роботи робилися раніше); 0 otherwise.

- appointment_date: the service appointment date/time, resolved to an absolute
  date where possible using the CALL DATE from the top of this prompt.
  Apply these rules in order:
  1. Exact date mentioned (e.g. "15 листопада", "5-го числа о 9:00"):
     → Format as "DD.MM.YYYY" (e.g. "15.11.2024"), appending the time as
       "DD.MM.YYYY о HH:MM" if a time was explicitly stated.
       Use the CALL DATE year for context when only a day/month is given.
  2. Relative expression calculable from CALL DATE
     (e.g. "завтра", "в п'ятницю", "через три дні о 10:00"):
     → Calculate the exact calendar date from CALL DATE and output in the
       same "DD.MM.YYYY" (or "DD.MM.YYYY о HH:MM") format.
  3. Vague expression that cannot be resolved to a specific date
     (e.g. "наступного тижня", "колись потім", "пізніше"):
     → Output the exact phrase from the transcript verbatim.
  4. No appointment made or discussed:
     → Output "0" (the digit zero — never null, never an empty string).

- goodbye_end: 1 if the manager said goodbye politely and ended the call
  properly (завершення розмови, прощання); 0 if the call ended abruptly or
  without a proper farewell.

── OUTCOME ──────────────────────────────────────────────────────────────────

- result: outcome of the call — choose EXACTLY one value from this list
  (no other text is allowed):
    "Запис"                   — a service appointment was booked
    "Повторно консультація"   — follow-up consultation is needed
    "Передано іншому філіалу" — call was transferred to another branch
    "Передзвонити"            — manager or client will call back later
    "Інше"                    — any other outcome, or outcome is unclear
  Default to "Інше" when the outcome does not clearly fit the other options.

- spare_parts: source of spare parts for this job — choose EXACTLY one value
  from this list (no other text is allowed, never return null or ""):
    "Клієнта" — the client explicitly stated they supply their own parts
    "Наші"    — the service center supplies the parts, OR spare parts were
                not explicitly discussed at all (this is the default)

RULES
-----
- Base every assessment solely on the transcript text.
- Do not invent details that are not present in the transcript.
- score must equal 1 when is_call_ok is true, and 0 when false.
- red_flag_comment must be null when is_call_ok is true.
- All binary checklist fields must be exactly 0 or 1 — never null or any
  other value.
- appointment_date: use the CALL DATE to resolve relative/partial dates into
  "DD.MM.YYYY" format. Output verbatim only when the date is genuinely
  uncalculable. Output "0" only when there is no appointment at all.
- result must be one of the five allowed values listed above — never free text.
- spare_parts must be exactly "Клієнта" or "Наші" — never free text, never
  null, never an empty string. Default to "Наші" whenever parts were not
  explicitly discussed.
- work_type must be exactly one of the values defined in the JSON schema enum —
  never a synonym, abbreviation, or any other text.
"""


def _build_system_prompt(call_date: str = "") -> str:
    """Return the full system prompt, optionally prepending a call-date header.

    When *call_date* is provided (ISO format ``"YYYY-MM-DD"``), a short
    context block is prepended so the model can resolve relative date
    expressions such as "завтра" or "в п'ятницю" to absolute calendar dates.

    When *call_date* is empty the static body is returned unchanged — all
    other fields are unaffected.
    """
    if call_date:
        header = (
            f"CALL DATE: {call_date}\n"
            f"Use this date as the reference point when resolving relative or "
            f"partial appointment date expressions from the transcript "
            f"(e.g. 'завтра' → the calendar day after {call_date}).\n\n"
        )
        return header + _SYSTEM_PROMPT_BODY
    return _SYSTEM_PROMPT_BODY

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

    def analyze_transcript(
        self,
        transcript: str,
        call_date: str = "",
    ) -> CallAnalysis:
        """Analyze *transcript* and return a structured ``CallAnalysis``.

        Args:
            transcript: The full plain-text transcript of the call.
            call_date:  ISO date string of the call (``"YYYY-MM-DD"``), used
                        by the model to resolve relative appointment expressions
                        like "завтра" or "в п'ятницю" into absolute dates.
                        Pass an empty string (default) to omit date context.

        Returns:
            A validated ``CallAnalysis`` Pydantic model instance.

        Raises:
            AnalysisError: if the API call fails, or if the model refuses
                to produce a structured response (``parsed`` is ``None``).
        """
        logger.info(
            f"Analysing transcript ({len(transcript)} chars) "
            f"with {self._MODEL}"
            + (f" [call_date={call_date}]" if call_date else "") + "."
        )

        system_prompt = _build_system_prompt(call_date)

        try:
            completion = self._client.beta.chat.completions.parse(
                model=self._MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
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
