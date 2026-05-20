from __future__ import annotations

from pydantic import BaseModel, Field


class CallAnalysis(BaseModel):
    """Structured result of a QA analysis performed on a single call transcript.

    Field descriptions are intentionally verbose because they are embedded
    verbatim into the JSON Schema that OpenAI Structured Outputs sends to the
    model — richer descriptions produce more accurate extractions.
    """

    has_recording: bool = Field(
        description=(
            "True if the transcript contains a real conversation with audible "
            "speech from both parties. False if the file was silent, empty, or "
            "could not be transcribed."
        )
    )
    work_type: str = Field(
        description=(
            "The type of auto-service work discussed in the call, written in "
            "Ukrainian. For example: 'Комп\\'ютерна діагностика', 'Заміна масла', "
            "'Ремонт гальмівної системи'. Use 'Невідомо' if the topic is unclear."
        )
    )
    manager_evaluation: str = Field(
        description=(
            "1-3 sentence evaluation of the service manager's communication "
            "quality: clarity, politeness, correctness of the information "
            "provided, and professionalism."
        )
    )
    is_call_ok: bool = Field(
        description=(
            "True if the manager handled the call professionally and gave "
            "correct information. False if the manager was rude, gave wrong "
            "information, missed the client's question, or behaved unprofessionally."
        )
    )
    red_flag_comment: str | None = Field(
        description=(
            "A concise explanation of the specific problem if is_call_ok is "
            "False (e.g. 'Менеджер не надав ціну на послугу'). "
            "Must be None when is_call_ok is True."
        )
    )
    score: int = Field(
        ge=0,
        le=1,
        description="1 when is_call_ok is True, 0 when is_call_ok is False.",
    )
