from __future__ import annotations

import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
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
# Analysis-row column layout — matches the Sheet1 template (0-based)
#
# Col  Header (Ukrainian)                    Populated from
#  0   Дата                               ← date_str
#  1   Тип звернення                      ← call_type (from filename)
#  2   Номер телефону                     ← phone_number (from filename)
#  3   Філія                              (left empty — not extracted)
#  4   Менеджер                           (left empty — not extracted)
#  5   Початок розмови, представлення     ← analysis.greeting_start
#  6   Чи дізнвся менеджер кузов          ← analysis.asked_car_body
#  7   Чи дізнався менеджер рік           ← analysis.asked_car_year
#  8   Чи дізнався менеджр пробіг         ← analysis.asked_mileage
#  9   Пропозиція про комплексну діагн.   ← analysis.offered_diagnostics
# 10   Дізнався які роботи робилися       ← analysis.asked_previous_works
# 11   Запис на сервіс, Дата              ← analysis.appointment_date
# 12   Завершення розмови прощання        ← analysis.goodbye_end
# 13   Яка робота з топ 100               ← analysis.work_type
# 14   Чи дотримувався всіх інструкцій    ← analysis.is_call_ok (1/0)
# 15   Яких рекомендацій не дотримувався  (left empty — not extracted)
# 16   Результат                          ← analysis.result
# 17   Оцінка                             ← analysis.score
# 18   Запчастини                         ← analysis.spare_parts
# 19   Коментар                           ← analysis.red_flag_comment
#                                            (always explicitly formatted)
# ---------------------------------------------------------------------------

_COL_DATE = 0
_COL_CALL_TYPE = 1
_COL_PHONE = 2
_COL_GREETING = 5
_COL_CAR_BODY = 6
_COL_CAR_YEAR = 7
_COL_MILEAGE = 8
_COL_DIAGNOSTICS = 9
_COL_PREV_WORKS = 10
_COL_APPOINTMENT = 11
_COL_GOODBYE = 12
_COL_WORK_TYPE = 13
_COL_IS_OK = 14      # Чи дотримувався всіх інструкцій (1/0)
_COL_RESULT = 16
_COL_SCORE = 17
_COL_SPARE_PARTS = 18
_COL_RED_FLAG = 19   # Коментар — always receives an explicit format call

_ROW_WIDTH = 20      # total named columns in the template

# ---------------------------------------------------------------------------
# Cell-formatting colour constants
# ---------------------------------------------------------------------------

# Bad-call comment cell: red background, white bold text
_RED_BG = {"red": 1.0, "green": 0.0, "blue": 0.0}
_WHITE_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}

# Good-call comment cell: explicit white/black reset so the new row never
# inherits red from a preceding bad-call row (Google Sheets copies the
# preceding row's format when INSERT_ROWS is used).
_WHITE_BG = {"red": 1.0, "green": 1.0, "blue": 1.0}
_DEFAULT_FG = {"red": 0.0, "green": 0.0, "blue": 0.0}

# Row border: solid black line applied to every side and inner-vertical
# divider so the newly appended row matches the table's existing grid lines.
_BLACK_COLOR = {"red": 0.0, "green": 0.0, "blue": 0.0}
_BLACK_BORDER = {"style": "SOLID", "color": _BLACK_COLOR}


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class CredentialsNotFoundError(FileNotFoundError):
    """Raised when credentials.json is absent and no cached token.json exists."""


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
        Both API clients are built once at construction time from injectable
        ``credentials_path`` and ``token_path``, making every public method
        independently testable via a patched ``_load_oauth_credentials``.
    """

    def __init__(
        self,
        credentials_path: Path = Path("credentials.json"),
        token_path: Path = Path("token.json"),
    ) -> None:
        credentials = _load_oauth_credentials(credentials_path, token_path, _SCOPES)
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
            spreadsheet_id: Target spreadsheet (file ID).
            sheet_id:        Numeric tab ID (``gid`` query param in the URL).
            row_index:       0-based row index of the target cell.
            column_index:    0-based column index of the target cell.
        """
        self._apply_cell_format(
            spreadsheet_id, sheet_id, row_index, column_index, bad=True
        )

    def format_cell_clear(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        row_index: int,
        column_index: int,
    ) -> None:
        """Explicitly reset a cell to white background and regular black text.

        Must be called for every *good* call row so that Google Sheets does not
        silently inherit the red background that ``INSERT_ROWS`` copies from a
        preceding bad-call row.

        Args:
            spreadsheet_id: Target spreadsheet (file ID).
            sheet_id:        Numeric tab ID (``gid`` query param in the URL).
            row_index:       0-based row index of the target cell.
            column_index:    0-based column index of the target cell.
        """
        self._apply_cell_format(
            spreadsheet_id, sheet_id, row_index, column_index, bad=False
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_cell_format(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        row_index: int,
        column_index: int,
        *,
        bad: bool,
    ) -> None:
        """Send a ``batchUpdate`` with two requests for the newly appended row.

        Request 0 — ``repeatCell``: sets the comment cell's background/text.
            ``bad=True``  → red background, white bold text (failed call)
            ``bad=False`` → white background, regular black text (passed call;
                            overrides any red inherited via ``INSERT_ROWS``)

        Request 1 — ``updateBorders``: draws solid black borders on every side
            of each cell in the full row (columns 0–_ROW_WIDTH) so the new row
            matches the visual grid of the existing table rows.

        Both requests are sent in a single ``batchUpdate`` call (one round-trip).
        The ``requests`` list is constructed fresh every call so no state leaks
        between rows.  ``sheetId`` (numeric tab gid) lives inside the ``range``
        objects; ``spreadsheetId`` (file ID) is the outer kwarg — both are
        required by the API and serve different purposes.
        """
        bg = _RED_BG if bad else _WHITE_BG
        fg = _WHITE_FG if bad else _DEFAULT_FG
        bold = bad
        label = "red" if bad else "white"

        logger.info(
            f"Formatting cell [{row_index},{column_index}] {label} "
            f"in sheet {sheet_id} of '{spreadsheet_id}'."
        )

        body = {
            "requests": [
                # ── request 0: comment-cell background / text colour ────────
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
                                "backgroundColor": bg,
                                "textFormat": {
                                    "bold": bold,
                                    "foregroundColor": fg,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(backgroundColor,textFormat)",
                    }
                },
                # ── request 1: solid black borders across the full row ──────
                # Draws top / bottom / left / right outer borders and inner
                # vertical cell dividers so the new row matches the grid lines
                # of the existing table rows.
                {
                    "updateBorders": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_index,
                            "endRowIndex": row_index + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": _ROW_WIDTH,
                        },
                        "top": _BLACK_BORDER,
                        "bottom": _BLACK_BORDER,
                        "left": _BLACK_BORDER,
                        "right": _BLACK_BORDER,
                        "innerVertical": _BLACK_BORDER,
                    }
                },
            ]
        }

        try:
            self._sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body,
            ).execute()
        except HttpError as exc:
            logger.error(
                f"batchUpdate formatting failed for '{spreadsheet_id}': {exc}"
            )
            raise SheetsServiceError(
                f"Failed to apply {label} formatting to cell "
                f"[{row_index},{column_index}] in spreadsheet '{spreadsheet_id}'."
            ) from exc

        logger.info(f"{label.capitalize()} formatting applied.")

    def append_analysis_result(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        sheet_id: int,
        date_str: str,
        analysis: CallAnalysis,
        call_type: str = "",
        phone_number: str = "",
    ) -> None:
        """Append one analysis row aligned to the Sheet1 template columns,
        then unconditionally apply explicit formatting to the Коментар cell.

        The row is exactly ``_ROW_WIDTH`` (20) cells wide.  Every unfilled
        position is an empty string ``""`` so columns never shift.

        Filled columns:
             0  Дата              ← date_str
             1  Тип звернення     ← call_type ("Вхідний" / "Вихідний" / "")
             2  Номер телефону    ← phone_number
             5  Представлення     ← analysis.greeting_start
             6  Кузов             ← analysis.asked_car_body
             7  Рік               ← analysis.asked_car_year
             8  Пробіг            ← analysis.asked_mileage
             9  Діагностика       ← analysis.offered_diagnostics
            10  Попередні роботи  ← analysis.asked_previous_works
            11  Запис, Дата       ← analysis.appointment_date ("0" when none)
            12  Прощання          ← analysis.goodbye_end
            13  Яка робота        ← analysis.work_type
            14  Чи дотримувався   ← int(analysis.is_call_ok) (1 / 0)
            16  Результат         ← analysis.result (strict Literal enum)
            17  Оцінка            ← analysis.score
            18  Запчастини        ← analysis.spare_parts (strict Literal enum)
            19  Коментар          ← analysis.red_flag_comment or ""

        Formatting (always issued, never conditional):
            bad  call (is_call_ok=False): col 19 → red background, white bold text
            good call (is_call_ok=True):  col 19 → explicit white background,
                regular black text — prevents inheriting the red format that
                Google Sheets copies from the preceding row when INSERT_ROWS
                inserts a new row above an existing red cell.

        The ``startRowIndex`` / ``endRowIndex`` are derived from the
        ``updatedRange`` the Sheets API returns in the append response, which
        already accounts for the template's two header rows.
        """
        row: list = [""] * _ROW_WIDTH
        row[_COL_DATE] = date_str
        row[_COL_CALL_TYPE] = call_type
        row[_COL_PHONE] = phone_number
        row[_COL_GREETING] = analysis.greeting_start
        row[_COL_CAR_BODY] = analysis.asked_car_body
        row[_COL_CAR_YEAR] = analysis.asked_car_year
        row[_COL_MILEAGE] = analysis.asked_mileage
        row[_COL_DIAGNOSTICS] = analysis.offered_diagnostics
        row[_COL_PREV_WORKS] = analysis.asked_previous_works
        row[_COL_APPOINTMENT] = analysis.appointment_date  # "0" when no appointment
        row[_COL_GOODBYE] = analysis.goodbye_end
        row[_COL_WORK_TYPE] = analysis.work_type
        row[_COL_IS_OK] = int(analysis.is_call_ok)
        row[_COL_RESULT] = analysis.result  # strict Literal enum value
        row[_COL_SCORE] = analysis.score
        row[_COL_SPARE_PARTS] = analysis.spare_parts  # strict Literal enum value
        row[_COL_RED_FLAG] = analysis.red_flag_comment or ""

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

        # Always format — never skip for good calls.
        # Skipping would leave the new row's Коментар cell red when the
        # immediately preceding row was a bad call (INSERT_ROWS inheritance).
        updated_range: str = response["updates"]["updatedRange"]
        row_index = _parse_row_index(updated_range)
        if analysis.is_call_ok:
            self.format_cell_clear(
                spreadsheet_id=spreadsheet_id,
                sheet_id=sheet_id,
                row_index=row_index,
                column_index=_COL_RED_FLAG,
            )
        else:
            self.format_cell_red(
                spreadsheet_id=spreadsheet_id,
                sheet_id=sheet_id,
                row_index=row_index,
                column_index=_COL_RED_FLAG,
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _load_oauth_credentials(
    credentials_path: Path,
    token_path: Path,
    scopes: list[str],
) -> Credentials:
    """Return valid OAuth 2.0 credentials, refreshing or re-authorising as needed.

    Flow:
        1. If ``token_path`` exists, load the cached token.
        2. If the token is still valid, return it immediately.
        3. If the token is expired but has a refresh token, refresh silently.
        4. Otherwise open a browser window to run the full OAuth Desktop flow.
        5. Persist the (new/refreshed) token back to ``token_path``.

    Raises:
        CredentialsNotFoundError: when the OAuth flow is needed but
            ``credentials_path`` does not exist.
    """
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        logger.debug(f"Loaded cached OAuth token from '{token_path}'.")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("OAuth token expired — refreshing silently ...")
            creds.refresh(Request())
            logger.info("Token refreshed successfully.")
        else:
            if not credentials_path.exists():
                raise CredentialsNotFoundError(
                    f"OAuth credentials file not found at '{credentials_path}'. "
                    "Download the OAuth 2.0 Desktop App client JSON from the "
                    "Google Cloud Console and place it at that path."
                )
            logger.info(
                "No valid token found — starting OAuth flow "
                "(a browser window will open) ..."
            )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), scopes
            )
            creds = flow.run_local_server(port=0)
            logger.info("OAuth authentication complete.")

        token_path.write_text(creds.to_json())
        logger.debug(f"OAuth token saved to '{token_path}'.")

    return creds


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
