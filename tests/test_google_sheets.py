"""Tests for GoogleSheetsService.

Strategy
--------
- ``_load_oauth_credentials`` is patched at the module level in the
  ``sheets_service`` fixture, completely bypassing all auth logic for the
  method-behaviour tests.
- Credential-loading tests call ``_load_oauth_credentials`` directly and
  patch only its internal dependencies at the module-level import path.
- ``build`` is called twice in ``__init__``: once for 'drive' and once for
  'sheets'.  A ``side_effect`` function dispatches the correct MagicMock to
  each call so tests can configure the two APIs independently.
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
    CredentialsNotFoundError,
    GoogleSheetsService,
    SheetsServiceError,
    _load_oauth_credentials,
    _parse_row_index,
    _BLACK_BORDER,
    _BLACK_COLOR,
    _COL_APPOINTMENT,
    _COL_CAR_BODY,
    _COL_CAR_YEAR,
    _COL_CALL_TYPE,
    _COL_DATE,
    _COL_DIAGNOSTICS,
    _COL_GOODBYE,
    _COL_GREETING,
    _COL_IS_OK,
    _COL_MILEAGE,
    _COL_PHONE,
    _COL_PREV_WORKS,
    _COL_RED_FLAG,
    _COL_RESULT,
    _COL_SCORE,
    _COL_SPARE_PARTS,
    _COL_WORK_TYPE,
    _DEFAULT_FG,
    _ROW_WIDTH,
    _SCOPES,
    _WHITE_BG,
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
    work_type="Заміна Оливи ДВЗ",
    manager_evaluation="Менеджер відповів чітко та ввічливо.",
    is_call_ok=True,
    red_flag_comment=None,
    score=1,
    greeting_start=1,
    asked_car_body=1,
    asked_car_year=1,
    asked_mileage=0,
    offered_diagnostics=1,
    asked_previous_works=1,
    appointment_date="Вівторок о 10:00",
    goodbye_end=1,
    result="Запис",
    spare_parts="Наші",
)

_BAD_ANALYSIS = CallAnalysis(
    has_recording=True,
    work_type="Компʼютерна діагностика",
    manager_evaluation="Менеджер не надав ціну.",
    is_call_ok=False,
    red_flag_comment="Менеджер не відповів на питання про вартість послуги.",
    score=0,
    greeting_start=1,
    asked_car_body=0,
    asked_car_year=0,
    asked_mileage=0,
    offered_diagnostics=0,
    asked_previous_works=0,
    appointment_date="0",
    goodbye_end=1,
    result="Інше",
    spare_parts="Наші",
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
def mock_drive() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_sheets() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def sheets_service(
    mock_drive: MagicMock,
    mock_sheets: MagicMock,
    mocker: pytest_mock.MockerFixture,
) -> GoogleSheetsService:
    """A fully-mocked GoogleSheetsService — auth is bypassed entirely."""
    mocker.patch(
        "app.services.google_sheets._load_oauth_credentials",
        return_value=MagicMock(),
    )

    def _build_dispatcher(service: str, version: str, credentials=None) -> MagicMock:
        return mock_drive if service == "drive" else mock_sheets

    mocker.patch("app.services.google_sheets.build", side_effect=_build_dispatcher)
    return GoogleSheetsService()


# ---------------------------------------------------------------------------
# Credentials loading  (_load_oauth_credentials tested directly)
# ---------------------------------------------------------------------------


class TestCredentialsLoading:
    def test_raises_when_no_token_and_no_credentials_file(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(CredentialsNotFoundError, match="credentials.json"):
            _load_oauth_credentials(
                credentials_path=tmp_path / "credentials.json",
                token_path=tmp_path / "token.json",
                scopes=_SCOPES,
            )

    def test_returns_valid_token_without_running_flow(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture
    ) -> None:
        token_file = tmp_path / "token.json"
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = True
        mocker.patch(
            "app.services.google_sheets.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        )

        result = _load_oauth_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token_file,
            scopes=_SCOPES,
        )
        assert result is mock_creds

    def test_builds_both_drive_and_sheets_clients(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mocker.patch(
            "app.services.google_sheets._load_oauth_credentials",
            return_value=MagicMock(),
        )
        mock_build = mocker.patch(
            "app.services.google_sheets.build",
            return_value=MagicMock(),
        )
        GoogleSheetsService()

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

    def test_batch_contains_update_borders_request(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """batchUpdate must include an updateBorders request alongside repeatCell."""
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=5)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        assert len(body["requests"]) == 2
        assert "updateBorders" in body["requests"][1]

    def test_borders_cover_full_row_width(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Border range must span all _ROW_WIDTH columns for the given row."""
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=5)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        border_range = body["requests"][1]["updateBorders"]["range"]

        assert border_range["sheetId"] == SHEET_ID
        assert border_range["startRowIndex"] == 4
        assert border_range["endRowIndex"] == 5
        assert border_range["startColumnIndex"] == 0
        assert border_range["endColumnIndex"] == _ROW_WIDTH

    def test_borders_are_solid_black(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Every border side must be SOLID with a black colour."""
        sheets_service.format_cell_red(SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0)

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        borders = body["requests"][1]["updateBorders"]

        for side in ("top", "bottom", "left", "right", "innerVertical"):
            assert borders[side] == _BLACK_BORDER, f"Border side '{side}' mismatch"
            assert borders[side]["style"] == "SOLID"
            assert borders[side]["color"] == _BLACK_COLOR


# ---------------------------------------------------------------------------
# format_cell_clear
# ---------------------------------------------------------------------------


class TestFormatCellClear:
    def test_calls_batch_update_once(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=19
        )

        mock_sheets.spreadsheets.return_value.batchUpdate.assert_called_once()

    def test_passes_correct_spreadsheet_id(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=19
        )

        kwargs = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs
        assert kwargs["spreadsheetId"] == SPREADSHEET_ID

    def test_repeatCell_range_matches_row_and_column(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=19
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        cell_range = body["requests"][0]["repeatCell"]["range"]

        assert cell_range["sheetId"] == SHEET_ID
        assert cell_range["startRowIndex"] == 4
        assert cell_range["endRowIndex"] == 5
        assert cell_range["startColumnIndex"] == 19
        assert cell_range["endColumnIndex"] == 20

    def test_background_color_is_white(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]

        assert fmt["backgroundColor"] == _WHITE_BG
        assert fmt["backgroundColor"]["red"] == 1.0
        assert fmt["backgroundColor"]["green"] == 1.0
        assert fmt["backgroundColor"]["blue"] == 1.0

    def test_text_is_not_bold_and_black(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        text_fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"]

        assert text_fmt["bold"] is False
        assert text_fmt["foregroundColor"] == _DEFAULT_FG
        assert text_fmt["foregroundColor"]["red"] == 0.0
        assert text_fmt["foregroundColor"]["green"] == 0.0
        assert text_fmt["foregroundColor"]["blue"] == 0.0

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
            sheets_service.format_cell_clear(SPREADSHEET_ID, SHEET_ID, 0, 0)

    def test_request_body_is_isolated_per_call(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Each call must build a fresh body dict — no shared mutable state."""
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=2, column_index=19
        )
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=5, column_index=19
        )

        calls = mock_sheets.spreadsheets.return_value.batchUpdate.call_args_list
        assert len(calls) == 2

        row_first = calls[0].kwargs["body"]["requests"][0]["repeatCell"]["range"][
            "startRowIndex"
        ]
        row_second = calls[1].kwargs["body"]["requests"][0]["repeatCell"]["range"][
            "startRowIndex"
        ]
        assert row_first == 2
        assert row_second == 5

    def test_batch_contains_update_borders_request(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """batchUpdate must include an updateBorders request alongside repeatCell."""
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=19
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        assert len(body["requests"]) == 2
        assert "updateBorders" in body["requests"][1]

    def test_borders_cover_full_row_width(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Border range must span all _ROW_WIDTH columns for the given row."""
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=4, column_index=19
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        border_range = body["requests"][1]["updateBorders"]["range"]

        assert border_range["sheetId"] == SHEET_ID
        assert border_range["startRowIndex"] == 4
        assert border_range["endRowIndex"] == 5
        assert border_range["startColumnIndex"] == 0
        assert border_range["endColumnIndex"] == _ROW_WIDTH

    def test_borders_are_solid_black(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Every border side must be SOLID with a black colour."""
        sheets_service.format_cell_clear(
            SPREADSHEET_ID, SHEET_ID, row_index=0, column_index=0
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        borders = body["requests"][1]["updateBorders"]

        for side in ("top", "bottom", "left", "right", "innerVertical"):
            assert borders[side] == _BLACK_BORDER, f"Border side '{side}' mismatch"
            assert borders[side]["style"] == "SOLID"
            assert borders[side]["color"] == _BLACK_COLOR


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
    def test_ok_call_appends_row_and_clears_comment_cell(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Good calls must trigger format_cell_clear — never skip the batchUpdate.

        Skipping it would let the new row inherit red from a preceding bad-call
        row because Google Sheets copies formatting when INSERT_ROWS is used.
        """
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:T5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        mock_sheets.spreadsheets.return_value.values.return_value.append.assert_called_once()
        mock_sheets.spreadsheets.return_value.batchUpdate.assert_called_once()

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
        assert fmt["backgroundColor"] == _WHITE_BG
        assert fmt["textFormat"]["bold"] is False

    def test_bad_call_appends_row_and_formats_red(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A7:T7")

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
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A7:T7")

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
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A3:T3")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        cell_range = body["requests"][0]["repeatCell"]["range"]
        assert cell_range["startColumnIndex"] == _COL_RED_FLAG
        assert cell_range["endColumnIndex"] == _COL_RED_FLAG + 1

    def test_row_has_correct_width(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert len(row) == _ROW_WIDTH

    def test_row_maps_date_to_col_date(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_DATE] == DATE_STR

    def test_row_maps_call_type_and_phone_to_correct_columns(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID,
            SHEET_NAME,
            SHEET_ID,
            DATE_STR,
            _OK_ANALYSIS,
            call_type="Вхідний",
            phone_number="380671234567",
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_CALL_TYPE] == "Вхідний"
        assert row[_COL_PHONE] == "380671234567"

    def test_row_maps_analysis_fields_to_template_columns(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_WORK_TYPE] == _OK_ANALYSIS.work_type
        assert row[_COL_SCORE] == _OK_ANALYSIS.score
        assert row[_COL_RED_FLAG] == ""   # no comment for an OK call

    def test_row_maps_checklist_fields_to_template_columns(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """All 10 newly-extracted fields land in their exact template indices."""
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]

        assert row[_COL_GREETING] == _OK_ANALYSIS.greeting_start
        assert row[_COL_CAR_BODY] == _OK_ANALYSIS.asked_car_body
        assert row[_COL_CAR_YEAR] == _OK_ANALYSIS.asked_car_year
        assert row[_COL_MILEAGE] == _OK_ANALYSIS.asked_mileage
        assert row[_COL_DIAGNOSTICS] == _OK_ANALYSIS.offered_diagnostics
        assert row[_COL_PREV_WORKS] == _OK_ANALYSIS.asked_previous_works
        assert row[_COL_APPOINTMENT] == _OK_ANALYSIS.appointment_date
        assert row[_COL_GOODBYE] == _OK_ANALYSIS.goodbye_end
        assert row[_COL_RESULT] == _OK_ANALYSIS.result
        assert row[_COL_SPARE_PARTS] == _OK_ANALYSIS.spare_parts

    def test_row_maps_absent_text_fields_correctly(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Absent-value fields land with their canonical placeholders (never None).

        - appointment_date with no appointment → '0' (Google Sheets validation rule)
        - result → valid Literal value from the enum
        - spare_parts with no parts discussed → ''
        """
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A4:T4")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]

        assert row[_COL_APPOINTMENT] == "0"
        assert row[_COL_RESULT] == _BAD_ANALYSIS.result
        assert row[_COL_SPARE_PARTS] == "Наші"

    def test_row_maps_red_flag_comment_to_comment_column(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:T5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_RED_FLAG] == _BAD_ANALYSIS.red_flag_comment

    def test_row_maps_is_call_ok_to_col_is_ok(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_IS_OK] == int(_OK_ANALYSIS.is_call_ok)  # 1

    def test_row_maps_bad_is_call_ok_as_zero(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A4:T4")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        assert row[_COL_IS_OK] == int(_BAD_ANALYSIS.is_call_ok)  # 0

    def test_bad_call_comment_cell_is_formatted_red(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A6:T6")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _BAD_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        fmt = body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
        assert fmt["backgroundColor"]["red"] == 1.0
        assert fmt["backgroundColor"]["green"] == 0.0
        assert fmt["backgroundColor"]["blue"] == 0.0
        assert fmt["textFormat"]["bold"] is True

    def test_unextracted_columns_are_empty(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Cols 3–4 (Філія, Менеджер) and col 15 (Яких рекомендацій) are not
        extracted by the bot and must always be empty strings."""
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A2:T2")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        kwargs = (
            mock_sheets.spreadsheets.return_value.values.return_value.append.call_args.kwargs
        )
        row = kwargs["body"]["values"][0]
        for col_idx in (3, 4, 15):
            assert row[col_idx] == "", f"Expected col {col_idx} to be empty"

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

    def test_append_result_batchupdate_contains_borders_request(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """append_analysis_result must issue a batchUpdate that includes borders."""
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:T5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        assert len(body["requests"]) == 2
        assert "updateBorders" in body["requests"][1]

    def test_append_result_borders_span_full_row(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Border range in append_analysis_result must cover all 20 columns."""
        # Row 5 in A1 notation → row_index 4 (0-based)
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:T5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        border_range = body["requests"][1]["updateBorders"]["range"]

        assert border_range["sheetId"] == SHEET_ID
        assert border_range["startRowIndex"] == 4
        assert border_range["endRowIndex"] == 5
        assert border_range["startColumnIndex"] == 0
        assert border_range["endColumnIndex"] == _ROW_WIDTH

    def test_append_result_borders_are_solid_black(
        self,
        sheets_service: GoogleSheetsService,
        mock_sheets: MagicMock,
    ) -> None:
        """Borders produced by append_analysis_result must be SOLID black."""
        _configure_append_response(mock_sheets, f"{SHEET_NAME}!A5:T5")

        sheets_service.append_analysis_result(
            SPREADSHEET_ID, SHEET_NAME, SHEET_ID, DATE_STR, _OK_ANALYSIS
        )

        body = mock_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        borders = body["requests"][1]["updateBorders"]

        for side in ("top", "bottom", "left", "right", "innerVertical"):
            assert borders[side] == _BLACK_BORDER, f"Border side '{side}' mismatch"
            assert borders[side]["style"] == "SOLID"
            assert borders[side]["color"] == _BLACK_COLOR


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
