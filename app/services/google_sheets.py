from __future__ import annotations

import re
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.logger import logger
from app.models.schemas import CallAnalysis

# ---------------------------------------------------------------------------
# Scopes — Drive needed for files().copy(); Sheets for read/write
# ---------------------------------------------------------------------------

_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ---------------------------------------------------------------------------
# Analysis-row column layout (0-based)
# Changing the order here automatically flows through format_cell_red.
# ---------------------------------------------------------------------------

_COL_DATE = 0
_COL_HAS_RECORDING = 1
_COL_WORK_TYPE = 2
_COL_EVALUATION = 3
_COL_IS_OK = 4
_COL_RED_FLAG = 5   # cell that gets red background for bad calls
_COL_SCORE = 6

# ---------------------------------------------------------------------------
# Red-cell formatting constants
# ---------------------------------------------------------------------------

_RED_BG = {"red": 1.0, "green": 0.0, "blue": 0.0}
_WHITE_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class SheetsServiceError(RuntimeError):
    """Wraps any ``HttpError`` raised by the Sheets or Drive API.

    The original ``HttpError`` is always chained as ``__cause__``.
    """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GoogleSheetsService:
    """Manages Google Sheets operations for the QA pipeline.

    Responsibilities (SRP):
        - copy a template spreadsheet (Drive API)
        - append rows of analysis data (Sheets API)
        - apply red-cell formatting to bad-call comment cells (Sheets API)
        - orchestrate all three into a single ``append_analysis_result`` call

    DIP note:
        Both API clients are built once at construction time from an injectable
        ``credentials_path``, making every public method independently testable
        via a patched ``build`` return value.
    """

    def __init__(
        self,
        credentials_path: Path = Path("credentials.json"),
    ) -> None:
        credentials = _load_service_account_credentials(credentials_path)
        self._drive = build("drive", "v3", credentials=credentials)
        self._sheets = build("sheets", "v4", credentials=credentials)
        logger.info("GoogleSheetsService initialised.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def copy_spreadsheet(self, template_file_id: str, new_title: str) -> str:
        """Duplicate *template_file_id* in Drive and return the new spreadsheet id.

        Uses Drive v3 ``files().copy()`` so the new file inherits the template's
        sheet structure and header row without any extra formatting calls.
        """
        logger.info(f"Copying template '{template_file_id}' → '{new_title}'.")
        try:
            response: dict = (
                self._drive.files()
                .copy(
                    fileId=template_file_id,
                    body={"name": new_title},
                    fields="id",
                )
                .execute()
            )
        except HttpError as exc:
            logger.error(f"Drive copy failed for template '{template_file_id}': {exc}")
            raise SheetsServiceError(
                f"Failed to copy template spreadsheet '{template_file_id}'."
            ) from exc

        spreadsheet_id: str = response["id"]
        logger.info(f"Spreadsheet copied — new id: {spreadsheet_id}")
        return spreadsheet_id

    def append_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        values: list,
    ) -> None:
        """Append *values* as a new row at the end of *sheet_name*.

        ``valueInputOption="USER_ENTERED"`` lets Sheets interpret booleans,
        numbers, and date strings naturally (no need for RAW escaping).
        ``insertDataOption="INSERT_ROWS"`` ensures an existing last row is
        never overwritten.
        """
        logger.info(
            f"Appending {len(values)}-column row to "
            f"'{sheet_name}' in '{spreadsheet_id}'."
        )
        try:
            self._sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [values]},
            ).execute()
        except HttpError as exc:
            logger.error(f"Sheets append failed for '{spreadsheet_id}': {exc}")
            raise SheetsServiceError(
                f"Failed to append row to sheet '{sheet_name}' "
                f"in spreadsheet '{spreadsheet_id}'."
            ) from exc

        logger.info("Row appended successfully.")

    def format_cell_red(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        row_index: int,
        column_index: int,
    ) -> None:
        """Apply a red background and white bold text to a single cell.

        Args:
            spreadsheet_id: Target spreadsheet.
            sheet_id:        Numeric id of the sheet tab (``gid`` in the URL).
            row_index:       0-based row index of the cell.
            column_index:    0-based column index of the cell.
        """
        logger.info(
            f"Formatting cell [{row_index},{column_index}] red "
            f"in sheet {sheet_id} of '{spreadsheet_id}'."
        )
        body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_index,
                            "endRowIndex": row_index + 1,
                            "startColumnIndex": column_index,
                            "endColumnIndex": column_index + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": _RED_BG,
                                "textFormat": {
                                    "bold": True,
                                    "foregroundColor": _WHITE_FG,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                }
            ]
        }
        try:
            self._sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body,
            ).execute()
        except HttpError as exc:
            logger.error(f"batchUpdate formatting failed for '{spreadsheet_id}': {exc}")
            raise SheetsServiceError(
                f"Failed to apply red formatting to cell "
                f"[{row_index},{column_index}] in spreadsheet '{spreadsheet_id}'."
            ) from exc

        logger.info("Red formatting applied.")

    def append_analysis_result(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        sheet_id: int,
        date_str: str,
        analysis: CallAnalysis,
    ) -> None:
        """Append one analysis row and immediately format the comment cell red
        if the call was flagged as bad (``analysis.is_call_ok is False``).

        Column layout:
            A date | B has_recording | C work_type | D manager_evaluation |
            E is_call_ok | F red_flag_comment | G score

        The red-flag comment cell (column F) is formatted red when
        ``is_call_ok`` is False so reviewers can spot problems at a glance.
        """
        row: list = [
            date_str,
            analysis.has_recording,
            analysis.work_type,
            analysis.manager_evaluation,
            analysis.is_call_ok,
            analysis.red_flag_comment or "",
            analysis.score,
        ]

        logger.info(
            f"Appending analysis result for '{date_str}' "
            f"(is_call_ok={analysis.is_call_ok}) to '{spreadsheet_id}'."
        )

        try:
            response: dict = (
                self._sheets.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                )
                .execute()
            )
        except HttpError as exc:
            logger.error(
                f"Failed to append analysis row for '{date_str}': {exc}"
            )
            raise SheetsServiceError(
                f"Failed to append analysis result for '{date_str}'."
            ) from exc

        if not analysis.is_call_ok:
            updated_range: str = response["updates"]["updatedRange"]
            row_index = _parse_row_index(updated_range)
            self.format_cell_red(
                spreadsheet_id=spreadsheet_id,
                sheet_id=sheet_id,
                row_index=row_index,
                column_index=_COL_RED_FLAG,
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _load_service_account_credentials(
    credentials_path: Path,
) -> service_account.Credentials:
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Google API credentials file not found at '{credentials_path}'. "
            "Download the service-account JSON from the Google Cloud Console "
            "and place it at that path."
        )
    logger.debug(f"Loading service-account credentials from '{credentials_path}'.")
    return service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=_SCOPES,
    )


def _parse_row_index(updated_range: str) -> int:
    """Return the 0-based row index from a Sheets ``updatedRange`` string.

    Examples:
        "Sheet1!A5:G5"  → 4
        "Data!B12"      → 11
    """
    # Take the cell reference after '!', use the last part after ':' if present
    cell_ref = updated_range.split("!")[1].split(":")[-1]
    match = re.search(r"(\d+)$", cell_ref)
    if not match:
        raise ValueError(
            f"Cannot parse row number from updatedRange: '{updated_range}'"
        )
    return int(match.group(1)) - 1
