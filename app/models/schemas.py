from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# "Яка робота з топ 100" — exact dropdown values extracted from the Google
# Sheets template (Sheet1 column N data-validation list).
# The LLM must return one of these strings verbatim; the JSON Schema enum
# enforced by OpenAI Structured Outputs makes any other value impossible.
# ---------------------------------------------------------------------------

WorkType = Literal[
    # ── Fallback / catch-all ────────────────────────────────────────────
    "інший варіант",
    # ── Routine maintenance ─────────────────────────────────────────────
    "комплексне ТО",
    "Заміна Оливи ДВЗ",
    "Заміна масла в АКПП",
    "Заміна охолоджуючої рідини",
    "Заміна гальмівної рідини з прокачкою",
    "Промивка системи охолодження",
    # ── Diagnostics ─────────────────────────────────────────────────────
    "Компʼютерна діагностика",
    "Комплексна діагностика",
    "Діагностика ДВЗ",
    "Діагностика течії",
    "Ендоскопія двигуна",
    "Замір комрессії",
    "Димогенератор",
    "пошук підсосів/витоку",
    "Тестер витоку охолоджуючої рідини",
    # ── Filters ─────────────────────────────────────────────────────────
    "Заміна повітряного фільтра ДВЗ",
    "Заміна фільтру салону",
    "Заміна фільтра салону в моторному відділенні",
    "Заміна паливного фільтра дизель",
    # ── Suspension / steering ───────────────────────────────────────────
    "Заміна сайлентблоків",
    "Заміна амортизатора переднього",
    "Заміна амортизатора зд.",
    "Заміна стійки стаблізатора переднього",
    "Заміна плаваючого сайлентблока.",
    "Заміна пильовика амортизатора",
    "Заміна З-х сайлентблоків редуктора",
    "Заміна втулки стабілізатора прд.",
    "Заміна кульової опори",
    "Заміна підшипника маточини",
    "Заміна рульвої тяги",
    "Заміна рульової тяги з наконечником",
    "Заміна ремкомплекту рейки",
    "Зняття / встановлення важіля прд.",
    # ── Brakes ──────────────────────────────────────────────────────────
    "Заміна гальмівних дисків та колодок",
    "Заміна гальмівних дисків та колодок зд.",
    "Замні та замовлення гальмівних колодок",
    "Розборка / зборка гальмівного супорта",
    # ── Engine / ignition ───────────────────────────────────────────────
    "Заміна свічок запалення",
    "Заміна свічок запалення N55",
    "Заміна свічок накалу",
    "Заміна котушки запалювання",
    "Заміна клапана Vanos",
    "Заміна ланцюгів ГРМ",
    "Заміна ланцюга ГРМ та масляного насосу N20",
    "Заміна прокладки маслостакана",
    "Знаття / встановлення маслостакана",
    "Заміна прокладки картера (піддону)",
    "Заміна КВКГ",
    "Заміна патрубка ОР",
    "Заміна приводного ремня",
    "Заміна термостату",
    "Заміна помпи",
    "Заміна пружини",
    "Заміна подушки ДВЗ",
    "Заміна еластичної муфти",
    "Зняття / встановлення впускного коллектора",
    "Зняття / встановлення впускного колектора M57",
    "Зняття / встановлення теплообміника",
    "Зняття / встановлення захисту двигуна",
    "Зняття / встановлення кардану",
    "Зняття,встановлення Турбокомпресора",
    "Зняття",
    # ── Cooling system ──────────────────────────────────────────────────
    "Заміна радіатору охолодження",
    "Заміна бачка ох. рідини",
    # ── Transmission / drivetrain ───────────────────────────────────────
    "Заміна оливи в передньому | задньому редукторі",
    "Знаття / встановлення піввісі",
    "Заміна подушки АКПП",
    "Заміна фланця роздавальної коробки",
    "Заміна пильовика ШРУСа",
    # ── Fuel system ─────────────────────────────────────────────────────
    "Зняття/встановлення паливних форсунок",
    # ── Electrical ──────────────────────────────────────────────────────
    "Заміна АКБ",
    "Реєстрація заміни АКБку",
    "Ремонт електропроводки",
    "Заміна лампочки",
    "Заміна датчика",
    "Заміна датчика кислороду (Лямбда)",
    # ── Seals / gaskets ─────────────────────────────────────────────────
    "Заміна переднього сальника колінвалу",
    "Заміна заднього сальника колінвалу та ремкомплект 8HP",
    # ── Body / trim ─────────────────────────────────────────────────────
    "Зняття / встановлення дверної карти",
    "Зняття / встановлення дверної ручки",
    "Зняття / встановлення переднього бампера",
    "Зняття / встановлення вихлопної труби",
    "Зняття / встановлення інтеркулера",
    "Зняття / встановлення повітряного патрубка",
    "Зняття / встановлення деталі",
    "Арматурні работи",
    "Мийка / чистка деталі",
    # ── Other ───────────────────────────────────────────────────────────
    "слюсарні роботи",
    "встановлення Турбокомпресора",
    "Протікання води в салон через гідроізоляцію дверних карт",
]


class CallAnalysis(BaseModel):
    """Structured result of a QA analysis performed on a single call transcript.

    Field descriptions are intentionally verbose because they are embedded
    verbatim into the JSON Schema that OpenAI Structured Outputs sends to the
    model — richer descriptions produce more accurate extractions.

    The binary checklist fields (greeting_start … goodbye_end) map directly to
    the corresponding columns in the QA Google Sheets template (cols 5–12).
    """

    # ------------------------------------------------------------------
    # Overall call quality
    # ------------------------------------------------------------------

    has_recording: bool = Field(
        description=(
            "True if the transcript contains a real conversation with audible "
            "speech from both parties. False if the file was silent, empty, or "
            "could not be transcribed."
        )
    )
    work_type: WorkType = Field(
        description=(
            "The primary auto-service work discussed in the call (Яка робота з "
            "топ 100). Choose EXACTLY one value from the allowed list — do not "
            "invent synonyms or free text. Use 'інший варіант' when no specific "
            "work from the list matches what was discussed."
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

    # ------------------------------------------------------------------
    # Script checklist  (template cols 5–12)
    # All binary fields: 1 = criterion was fulfilled, 0 = not fulfilled.
    # Defaults allow backward-compatible instantiation in tests; the LLM
    # always populates every field from the JSON Schema.
    # ------------------------------------------------------------------

    greeting_start: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager properly greeted the client and introduced "
            "themselves and/or the company at the very start of the call "
            "(Початок розмови, представлення). 0 if the manager skipped the "
            "greeting or introduction entirely."
        ),
    )
    asked_car_body: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager asked about the car body type / model variant "
            "(кузов автомобіля, e.g., sedan, hatchback, SUV, універсал). "
            "0 if the question was never asked."
        ),
    )
    asked_car_year: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager asked about the car's year of manufacture "
            "(рік автомобіля). 0 if the question was never asked."
        ),
    )
    asked_mileage: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager asked about the car's current mileage (пробіг). "
            "0 if the question was never asked."
        ),
    )
    offered_diagnostics: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager offered or mentioned a comprehensive / computer "
            "diagnostics service (пропозиція про комплексну діагностику). "
            "0 if diagnostics were never offered."
        ),
    )
    asked_previous_works: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager asked what work had been done on the car "
            "previously (дізнався які роботи робилися раніше). "
            "0 if the question was never asked."
        ),
    )
    appointment_date: str = Field(
        default="0",
        description=(
            "The date and/or time of a service appointment if one was discussed "
            "or scheduled during the call (Запис на сервіс, Дата). Write exactly "
            "as mentioned in the conversation (e.g., 'вівторок о 10:00', "
            "'15 травня'). Use '0' (the character zero) if no appointment was made."
        ),
    )

    @field_validator("appointment_date", mode="before")
    @classmethod
    def _coerce_empty_appointment(cls, v: object) -> object:
        """Normalise empty / whitespace-only strings to '0'.

        Guarantees the Google Sheets column never receives a blank cell even
        if the LLM deviates from the prompt and returns "" instead of "0".
        """
        if isinstance(v, str) and not v.strip():
            return "0"
        return v

    goodbye_end: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "1 if the manager properly said goodbye and ended the call "
            "politely (завершення розмови, прощання). "
            "0 if the call ended abruptly or without a proper farewell."
        ),
    )

    # ------------------------------------------------------------------
    # Outcome fields  (template cols 16, 18)
    # ------------------------------------------------------------------

    result: Literal[
        "Запис",
        "Повторно консультація",
        "Передано іншому філіалу",
        "Передзвонити",
        "Інше",
    ] = Field(
        default="Інше",
        description=(
            "Outcome of the call (Результат). Choose EXACTLY one of the "
            "allowed values — do not invent new text:\n"
            "  'Запис'                  — a service appointment was booked\n"
            "  'Повторно консультація'  — follow-up consultation needed\n"
            "  'Передано іншому філіалу'— call transferred to another branch\n"
            "  'Передзвонити'           — manager or client will call back\n"
            "  'Інше'                   — any other outcome or outcome unclear"
        ),
    )
    spare_parts: Literal["Клієнта", "Наші"] = Field(
        default="Наші",
        description=(
            "Spare parts source for this job (Запчастини). Choose EXACTLY one "
            "of the two allowed values — do not invent new text, never return "
            "null or an empty string:\n"
            "  'Клієнта' — the client explicitly stated they supply their own parts\n"
            "  'Наші'    — the service center supplies the parts, OR spare parts "
            "were not explicitly discussed (default)"
        ),
    )
