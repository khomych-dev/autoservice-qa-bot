"""Tests for GoogleSheetsService.

Strategy
--------
- ``build`` is called twice in ``__init__``: once for 'drive' and once for
  'sheets'.  A ``side_effect`` function dispatches the correct MagicMock to
  each call so tests can configure the two APIs independently.
- A real (but empty) ``credentials.json`` is written to ``tmp_path`` so that
  ``Path.exists()`` passes without patching the stdlib.
- ``HttpError`` instances are constructed with the real ``httplib2.Response``
  and ``content`` bytes that were confirmed by introspection.
- ``_parse_row_index`` is tested directly as a pure function.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import httplib2
import pytest
import pytest_mock
from googleapiclient.errors import HttpError

from app.models.schemas import CallAnalysis
from app.services.google_sheets import (
    GoogleSheetsService,
    SheetsServiceError,
    _parse_row_index,
    _COL_RED_FLAG,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "spreadsheet-id-abc"
TEMPLATE_ID = "template-id-xyz"
SHEET_NAME = "Results"
SHEET_ID = 0
DATE_STR = "2026-05-20"

_OK_ANALYSIS = CallAnalysis(
    has_recording=True,
    work_type="Заміна масла",
    manager_evaluation="Менеджер відповів чітко та ввічливо.",
    is_call_ok=True,
    red_flag_comment=None,
    score=1,
)

_BAD_ANALYSIS = CallAnalysis(
    has_recording=True,
    work_type="Комп'ютерна діагностика",
    manager_evaluation="Менеджер не надав ціну.",
    is_call_ok=False,
    red_flag_comment="Менеджер не відповів на питання про вартість послуги.",
    score=0,
)


# ---------------------------------------------------------------------------
# HttpError factory
# ---------------------------------------------------------------------------


def _http_error(status: int = 403, reason: str = "Forbidden") -> HttpError:
    resp = httplib2.Response({"status": status})
    return HttpError(
        resp=resp,
        content=reason.encode(),
        uri="https://sheets.googleapis.com/fake",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_credentials_file(tmp_path: Path) -> Path:
    p = tmp_path / "credentials.json"
    p.write_text('{"type": "service_account"}')
    return p


@pytest.fixture()
def mock_drive() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_sheets() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def sheets_service(
    fake_credentials_file: Path,
    mock_drive: MagicMock,
    mock_sheets: MagicMock,
    mocker: pytest_mock.MockerFixture,
) -> GoogleSheetsService:
    """A fully-mocked GoogleSheetsService."""
    mocker.patch(
        "app.services.google_sheets.service_account.Credentials.from_service_account_file",
        return_value=MagicMock(),
    )

    def _build_dispatcher(service: str, version: str, credentials=None) -> MagicMock:
        return mock_drive if service == "drive" else mock_sheets

    mocker.patch("app.services.google_sheets.build", side_effect=_build_dispatcher)
    return GoogleSheetsService(credentials_path=fake_credentials_file)


# ---------------------------------------------------------------------------
# Credentials loading
# ---------------------------------------------------------------------------


class TestCredentialsLoading:
    def test_raises_file_not_found_when_credentials_absent(self) -> None:
        with pytest.raises(FileNotFoundError, match="credentials.json"):
            GoogleSheetsService(credentials_path=Path("/no/such/credentials.json"))

    def test_builds_both_drive_and_sheets_clients(
        self,
        fake_credentials_file: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mocker.patch(
            "app.services.google_sheets.service_account.Credentials.from_service_account_file",
            return_value=MagicMock(),
        )
        mock_build = mocker.patch(
            "app.services.google_sheets.build",
            return_value=MagicMock(),
        )
        GoogleSheetsService(credentials_path=fake_credentials_file)

        service_names = [c.args[0] for c in mock_build.call_args_list]
        assert "drive" in service_names
        assert "sheets" in service_names


# ---------------------------------------------------------------------------
# copy_spreadsheet
# ---------------------------------------------------------------------------


class TestCopySpreadsheet:
    def test_returns_new_spreadsheet_id(
        self,
        sheets_service: GoogleSheetsService,
        mock_drive: MagicMock,
    ) -> None:
        mock_drive.files.return_value.copy.return_value.execute.return_value = {
            "id": "new-ss-id"
        }

        result = sheets_service.copy_spreadsheet(TEMPLATE_ID, "Report 2026")

        assert result == "new-ss-id"

    def test_passes_template_file_id(
        self,
        sheets_service: GoogleSheetsService,
        mock_drive: MagicMock,
    ) -> None:
        mock_drive.files.return_value.copy.return_value.execute.return_value = {
            "id": "x"
        }

        sheets_service.copy_spreadsheet(TEMPLATE_ID, "Any Title")

        kwargs = mock_drive.files.return_value.copy.call_args.kwargs
        assert kwargs["fileId"] == TEMPLATE_ID

    def test_passes_new_title_in_body(
        self,
        sheets_service: GoogleSheetsService,
        mock_drive: MagicMock,
    ) -> None:
        mock_drive.files.return_value.copy.return_value.execute.return_value = {
            "id": "x"
        }

        sheets_service.copy_spreadsheet(TEMPLATE_ID, "My Report")

        kwargs = mock_drive.files.return_value.copy.call_args.kwargs
        assert kwargs["body"]["name"] == "My Report"

    def test_raises_sheets_service_error_on_http_error(
        self,
        sheets_service: GoogleSheetsService,
        mock_drive: MagicMock,
    ) -> None:
        mock_drive.files.return_value.copy.return_value.execute.side_effect = (
            _http_error(403)
        )

        with pytest.raises(SheetsServiceError):
            sheets_service.copy_spreadsheet(TEMPLATE_ID, "Title")

    def test_chains_http_error_as_cause(
        self,
        sheets_service: GoogleSheetsService,
        mock_drive: MagicMock,
    ) -> None:
        original = _http_error(403)
        mock_drive.files.return_value.copy.return_value.execute.side_effect = original

        with pytest.raises(SheetsServiceError) as exc_info:
            sheets_service.copy_spreadsheet(TEMPLATE_ID, "Title")

        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# append_row
# ---------------------------------------------------------------------------


class TestAppendRow:
    def test_calls_values_append_once(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.append_row(SPREADSHEET_ID, SHEET_NAME, ["a", "b", "c"])

        mock_sheets.spreadsheets.return_value.values.return_value.append.assert_called_once()

    def test_passes_spreadsheet_id(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.append_row(SPREADSHEET_ID, SHEET_NAME, [1, 2])

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        assert kwargs["spreadsheetId"] == SPREADSHEET_ID

    def test_passes_user_entered_value_input_option(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.append_row(SPREADSHEET_ID, SHEET_NAME, [1])

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        assert kwargs["valueInputOption"] == "USER_ENTERED"

    def test_wraps_values_in_list_of_lists(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.append_row(SPREADSHEET_ID, SHEET_NAME, ["x", "y"])

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        assert kwargs["body"] == {"values": [["x", "y"]]}

    def test_raises_sheets_service_error_on_http_error(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        (
            mock_sheets.spreadsheets.return_value.values.return_value.append.return_value
            .execute.side_effect
        ) = _http_error(500)

        with pytest.raises(SheetsServiceError):
            sheets_service.append_row(SPREADSHEET_ID, SHEET_NAME, [1])


# ---------------------------------------------------------------------------
# format_cell_red
# ---------------------------------------------------------------------------


class TestFormatCellRed:
    def test_calls_batch_update_once(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=5)

        mock_sheets.spreadsheets.return_value.batchUpdate.assert_called_once()

    def test_passes_correct_spreadsheet_id(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=5)

        kwargs = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs
        assert kwargs["spreadsheetId"] == SPREADSHEET_ID

    def test_repeatCell_range_matches_row_and_column(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=5)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        cell_range = body["requests"][0]["repeatCell"]["range"]

        assert cell_range["sheetId"] == SHEET_ID
        assert cell_range["startRowIndex"] == 4
        assert cell_range["endRowIndex"] == 5
        assert cell_range["startColumnIndex"] == 5
        assert cell_range["endColumnIndex"] == 6

    def test_background_color_is_red(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]

        assert fmt["backgroundColor"]["red"] == 1.0
        assert fmt["backgroundColor"]["green"] == 0.0
        assert fmt["backgroundColor"]["blue"] == 0.0

    def test_text_is_white_and_bold(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        text_fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"]

        assert text_fmt["bold"] is True
        assert text_fmt["foregroundColor"]["red"] == 1.0
        assert text_fmt["foregroundColor"]["green"] == 1.0
        assert text_fmt["foregroundColor"]["blue"] == 1.0

    def test_raises_sheets_service_error_on_http_error(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        (
            mock_sheets.spreadsheets.return_value.batchUpdate.return_value
            .execute.side_effect
        ) = _http_error(403)

        with pytest.raises(SheetsServiceError):
            sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, 0, 0)


# ---------------------------------------------------------------------------
# append_analysis_result
# ---------------------------------------------------------------------------


def _configure_append_response(mock_sheets: MagicMock, updated_range: str) -> None:
    """Wire mock_sheets so values().append().execute() returns the given range."""
    (
        mock_sheets.spreadsheets.return_value.values.return_value.append.return_value
        .execute.return_value
    ) = {"updates": {"updatedRange": updated_range}}


class TestAppendAnalysisResult:
    def test_ok_call_appends_row_without_formatting(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:G5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        mock_sheets.spreadsheets.return_value.values.return_value.append.assert_called_once()
        mock_sheets.spreadsheets.return_value.batchUpdate.assert_not_called()

    def test_bad_call_appends_row_and_formats_red(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A7:G7")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        mock_sheets.spreadsheets.return_value.values.return_value.append.assert_called_once()
        mock_sheets.spreadsheets.return_value.batchUpdate.assert_called_once()

    def test_bad_call_red_formatting_targets_correct_row(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        # Row 7 in A1 notation → row_index 6 (0-based)
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A7:G7")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        cell_range = body["requests"][0]["repeatCell"]["range"]
        assert cell_range["startRowIndex"] == 6
        assert cell_range["endRowIndex"] == 7

    def test_bad_call_red_formatting_targets_red_flag_column(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A3:G3")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        cell_range = body["requests"][0]["repeatCell"]["range"]
        assert cell_range["startColumnIndex"] == _COL_RED_FLAG
        assert cell_range["endColumnIndex"] == _COL_RED_FLAG + 1

    def test_row_contains_date_as_first_element(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:G2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[0] == DATE_STR

    def test_row_contains_all_analysis_fields(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:G2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[1] == _OK_ANALYSIS.has_recording
        assert row[2] == _OK_ANALYSIS.work_type
        assert row[3] == _OK_ANALYSIS.manager_evaluation
        assert row[4] == _OK_ANALYSIS.is_call_ok
        assert row[6] == _OK_ANALYSIS.score

    def test_raises_sheets_service_error_on_http_error(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        (
            mock_sheets.spreadsheets.return_value.values.return_value.append.return_value
            .execute.side_effect
        ) = _http_error(500)

        with pytest.raises(SheetsServiceError):
            sheets_service.append_analysis_result(
                SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
            )


# ---------------------------------------------------------------------------
# _parse_row_index (pure function — tested in isolation)
# ---------------------------------------------------------------------------


class TestParseRowIndex:
    @pytest.mark.parametrize(
        "updated_range, expected",
        [
            ("Sheet1!A1:G1", 0),
            ("Sheet1!A5:G5", 4),
            ("Results!A12:G12", 11),
            ("My Sheet!B7", 6),
            ("Data!Z100:Z100", 99),
        ],
    )
    def test_parses_correctly(self, updated_range: str, expected: int) -> None:
        assert _parse_row_index(updated_range) == expected

    def test_raises_value_error_for_malformed_range(self) -> None:
        with pytest.raises(ValueError, match="updatedRange"):
            _parse_row_index("Sheet1!A:G")
