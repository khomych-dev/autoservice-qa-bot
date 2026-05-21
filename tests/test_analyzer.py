"""Tests for AnalyzerService.

Strategy
--------
- The ``openai.OpenAI`` client is constructor-injected, so every test receives
  a plain ``MagicMock`` — no module-level patching of ``openai`` is needed.
- The ``ParsedChatCompletion`` response is simulated by chaining MagicMock
  attributes to mirror the real access path:
      completion.choices[0].message.parsed  → CallAnalysis instance
      completion.choices[0].message.refusal → str | None
- Real ``openai`` exception instances are built (using ``httpx`` primitives
  already present as a transitive dependency) so that ``except`` clauses in
  the service are exercised via the actual MRO, not faked.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.models.schemas import CallAnalysis
from app.services.analyzer import AnalysisError, AnalyzerService

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_SAMPLE_TRANSCRIPT = (
    "Клієнт: Добрий день, хочу записатися на комп'ютерну діагностику.\n"
    "Менеджер: Добрий день! Звісно, підходьте у вівторок о 10:00, діагностика "
    "коштує 500 грн і займе приблизно годину."
)

_DUMMY_ANALYSIS = CallAnalysis(
    has_recording=True,
    work_type="Компʼютерна діагностика",
    manager_evaluation="Менеджер відповів ввічливо, надав точну ціну та час.",
    is_call_ok=True,
    red_flag_comment=None,
    score=1,
)

_FAKE_REQUEST = httpx.Request(
    "POST",
    "https://api.openai.com/v1/chat/completions",
)

# ---------------------------------------------------------------------------
# Exception factories (exact constructors confirmed via introspection)
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
    return openai.APIError("Internal server error.", _FAKE_REQUEST, body=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completion(
    parsed: CallAnalysis | None,
    refusal: str | None = None,
) -> MagicMock:
    """Build a MagicMock that mirrors ParsedChatCompletion[CallAnalysis]."""
    message = MagicMock()
    message.parsed = parsed
    message.refusal = refusal

    choice = MagicMock()
    choice.message = message

    completion = MagicMock()
    completion.choices = [choice]
    return completion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def service(mock_client: MagicMock) -> AnalyzerService:
    return AnalyzerService(client=mock_client)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAnalyzeTranscriptSuccess:
    def test_returns_call_analysis_instance(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        result = service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert isinstance(result, CallAnalysis)

    def test_returns_parsed_field_verbatim(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        result = service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert result is _DUMMY_ANALYSIS

    def test_all_fields_preserved(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        result = service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert result.has_recording is True
        assert result.work_type == "Компʼютерна діагностика"
        assert result.is_call_ok is True
        assert result.red_flag_comment is None
        assert result.score == 1

    def test_red_flag_fields_for_bad_call(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        bad_analysis = CallAnalysis(
            has_recording=True,
            work_type="Заміна Оливи ДВЗ",
            manager_evaluation="Менеджер не надав ціну та грубо відповів.",
            is_call_ok=False,
            red_flag_comment="Менеджер не надав інформацію про вартість послуги.",
            score=0,
        )
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            bad_analysis
        )

        result = service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert result.is_call_ok is False
        assert result.score == 0
        assert result.red_flag_comment is not None


# ---------------------------------------------------------------------------
# parse() call contract
# ---------------------------------------------------------------------------


class TestAnalyzeTranscriptCallContract:
    def test_calls_parse_with_correct_model(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        kwargs = mock_client.beta.chat.completions.parse.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"

    def test_calls_parse_with_call_analysis_response_format(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        kwargs = mock_client.beta.chat.completions.parse.call_args.kwargs
        assert kwargs["response_format"] is CallAnalysis

    def test_messages_include_system_prompt(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        kwargs = mock_client.beta.chat.completions.parse.call_args.kwargs
        roles = [m["role"] for m in kwargs["messages"]]
        assert "system" in roles

    def test_transcript_is_the_user_message(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        kwargs = mock_client.beta.chat.completions.parse.call_args.kwargs
        user_messages = [m for m in kwargs["messages"] if m["role"] == "user"]
        assert len(user_messages) == 1
        assert user_messages[0]["content"] == _SAMPLE_TRANSCRIPT

    def test_parse_called_exactly_once(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            _DUMMY_ANALYSIS
        )

        service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        mock_client.beta.chat.completions.parse.assert_called_once()


# ---------------------------------------------------------------------------
# Model refusal (parsed is None)
# ---------------------------------------------------------------------------


class TestAnalyzeTranscriptRefusal:
    def test_raises_analysis_error_when_parsed_is_none(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            parsed=None, refusal="I cannot process this content."
        )

        with pytest.raises(AnalysisError):
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

    def test_refusal_reason_is_in_error_message(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.return_value = _make_completion(
            parsed=None, refusal="Content policy violation."
        )

        with pytest.raises(AnalysisError, match="Content policy violation"):
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)


# ---------------------------------------------------------------------------
# API errors → AnalysisError
# ---------------------------------------------------------------------------


class TestAnalyzeTranscriptApiErrors:
    def test_rate_limit_error_raises_analysis_error(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.side_effect = _rate_limit_error()

        with pytest.raises(AnalysisError, match="Rate limit"):
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

    def test_rate_limit_error_chains_cause(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        original = _rate_limit_error()
        mock_client.beta.chat.completions.parse.side_effect = original

        with pytest.raises(AnalysisError) as exc_info:
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert exc_info.value.__cause__ is original

    def test_connection_error_raises_analysis_error(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.side_effect = _connection_error()

        with pytest.raises(AnalysisError, match="connect"):
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

    def test_connection_error_chains_cause(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        original = _connection_error()
        mock_client.beta.chat.completions.parse.side_effect = original

        with pytest.raises(AnalysisError) as exc_info:
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert exc_info.value.__cause__ is original

    def test_generic_api_error_raises_analysis_error(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        mock_client.beta.chat.completions.parse.side_effect = _api_error()

        with pytest.raises(AnalysisError):
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

    def test_generic_api_error_chains_cause(
        self,
        service: AnalyzerService,
        mock_client: MagicMock,
    ) -> None:
        original = _api_error()
        mock_client.beta.chat.completions.parse.side_effect = original

        with pytest.raises(AnalysisError) as exc_info:
            service.analyze_transcript(_SAMPLE_TRANSCRIPT)

        assert exc_info.value.__cause__ is original
