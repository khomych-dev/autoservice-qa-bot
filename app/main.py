from __future__ import annotations

import datetime
import re
from pathlib import Path

from app.core.config import settings
from app.core.logger import logger
from app.services.analyzer import AnalysisError, AnalyzerService
from app.services.google_drive import GoogleDriveService
from app.services.google_sheets import GoogleSheetsService, SheetsServiceError
from app.services.transcription import TranscriptionError, TranscriptionService

_DATA_DIR = Path("data")

# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

# Ukrainian phone numbers: 380XXXXXXXXX (12 digits) or 0XXXXXXXXX (10 digits)
_PHONE_RE = re.compile(r"380\d{9}|0\d{9}")

# Dates embedded in filenames — two common patterns:
#   YYYY-MM-DD / YYYY.MM.DD / YYYY_MM_DD  (ISO-ish, e.g. "2024-11-13")
#   DD-MM-YYYY / DD.MM.YYYY / DD_MM_YYYY  (European, e.g. "13.11.2024")
_DATE_RE_ISO = re.compile(r"(\d{4})[.\-_](\d{2})[.\-_](\d{2})")
_DATE_RE_EUR = re.compile(r"(\d{2})[.\-_](\d{2})[.\-_](\d{4})")

_INCOMING_KEYWORDS = ("incoming", "вхід", "in_", "_in_", "(in)")
_OUTGOING_KEYWORDS = ("outgoing", "вихід", "out_", "_out_", "(out)")


def _parse_filename(file_name: str) -> tuple[str, str]:
    """Return ``(call_type, phone_number)`` extracted from *file_name*.

    Call-type detection (case-insensitive):
        Incoming keywords → ``"Вхідний"``
        Outgoing keywords → ``"Вихідний"``
        No match          → ``""``

    Phone number extraction:
        Prefers 12-digit ``380XXXXXXXXX`` format; falls back to 10-digit
        ``0XXXXXXXXX``; returns ``""`` if neither is found.
    """
    name_lower = file_name.lower()

    if any(kw in name_lower for kw in _INCOMING_KEYWORDS):
        call_type = "Вхідний"
    elif any(kw in name_lower for kw in _OUTGOING_KEYWORDS):
        call_type = "Вихідний"
    else:
        call_type = ""

    phone_match = _PHONE_RE.search(file_name)
    phone_number = phone_match.group() if phone_match else ""

    return call_type, phone_number


def _parse_call_date(file_name: str, fallback: str) -> str:
    """Return an ISO date string (``"YYYY-MM-DD"``) for the call.

    Tries to extract a date from *file_name* in two formats:
    - ISO-ish:  ``YYYY-MM-DD``, ``YYYY.MM.DD``, ``YYYY_MM_DD``
    - European: ``DD-MM-YYYY``, ``DD.MM.YYYY``, ``DD_MM_YYYY``

    Falls back to *fallback* (typically today's date) if neither pattern
    matches or if the extracted date is not a valid calendar date.
    """
    m = _DATE_RE_ISO.search(file_name)
    if m:
        candidate = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    else:
        m = _DATE_RE_EUR.search(file_name)
        if m:
            candidate = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        else:
            return fallback

    try:
        datetime.date.fromisoformat(candidate)  # validate the date is real
        return candidate
    except ValueError:
        return fallback

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the Autoservice QA Bot pipeline.

    Execution order
    ---------------
    1.  Initialise all services (fail-fast — no point continuing without creds).
    2.  Copy the template spreadsheet to create today's report.
    3.  Fetch the list of audio files from the configured Drive folder.
    4.  For every audio file:
          a. Download to data/
          b. Transcribe via Whisper
          c. Analyse the transcript via GPT-4o-mini (Structured Outputs)
          d. Upload the plain-text transcript back to Drive
          e. Append the analysis row to the report spreadsheet
             (red cell formatting is applied automatically for bad calls)
          f. Delete the local audio file to keep disk usage low
    5.  Log a final summary.

    Error isolation
    ---------------
    Known service errors (``TranscriptionError``, ``AnalysisError``,
    ``SheetsServiceError``) are caught per-file and logged without a traceback —
    the cause is already described in the exception message.

    Any other unexpected exception is also caught per-file and logged *with*
    the full traceback so the root cause can be diagnosed.

    In both cases the pipeline advances to the next file.
    """
    logger.info("=" * 60)
    logger.info("Autoservice QA Bot — pipeline starting")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 1. Service initialisation (errors here abort the whole run)
    # ------------------------------------------------------------------
    logger.info("Initialising services ...")
    drive = GoogleDriveService()
    transcription = TranscriptionService()
    analyzer = AnalyzerService()
    sheets = GoogleSheetsService()
    logger.info("All services ready.")

    # ------------------------------------------------------------------
    # 2. Create today's report spreadsheet from the template
    # ------------------------------------------------------------------
    today: str = datetime.date.today().isoformat()
    report_title = f"QA Report — {today}"

    logger.info(f"Copying template → '{report_title}' ...")
    spreadsheet_id = sheets.copy_spreadsheet(
        template_file_id=settings.template_spreadsheet_id,
        new_title=report_title,
    )
    logger.info(f"Report spreadsheet ready: {spreadsheet_id}")

    # ------------------------------------------------------------------
    # 3. Fetch audio file list
    # ------------------------------------------------------------------
    logger.info(
        f"Fetching audio files from folder '{settings.source_drive_folder_id}' ..."
    )
    audio_files: list[dict] = drive.get_audio_files_metadata(
        settings.source_drive_folder_id
    )
    total = len(audio_files)

    if not total:
        logger.warning("No audio files found in the source folder. Nothing to do.")
        return

    logger.info(f"Found {total} audio file(s). Starting processing loop.")

    # ------------------------------------------------------------------
    # 4. Per-file processing loop
    # ------------------------------------------------------------------
    processed = 0
    failed = 0

    for idx, file_meta in enumerate(audio_files, start=1):
        file_id: str = file_meta["id"]
        file_name: str = file_meta["name"]
        prefix = f"[{idx}/{total}] '{file_name}'"

        logger.info(f"{prefix} — starting")
        local_path: Path | None = None
        call_type, phone_number = _parse_filename(file_name)
        call_date = _parse_call_date(file_name, fallback=today)
        logger.debug(
            f"{prefix} — parsed call_type='{call_type}' "
            f"phone='{phone_number}' call_date='{call_date}'"
        )

        try:
            # ── a. Download ────────────────────────────────────────────
            logger.info(f"{prefix} — downloading ...")
            local_path = drive.download_file(
                file_id=file_id,
                file_name=file_name,
                download_path=_DATA_DIR,
            )
            logger.info(f"{prefix} — saved to {local_path}")

            # ── b. Transcribe ──────────────────────────────────────────
            logger.info(f"{prefix} — transcribing ...")
            transcript: str = transcription.transcribe_audio(local_path)
            logger.info(
                f"{prefix} — transcription complete ({len(transcript)} chars)"
            )

            # ── c. Analyse ─────────────────────────────────────────────
            logger.info(f"{prefix} — analysing transcript ...")
            analysis = analyzer.analyze_transcript(transcript, call_date=call_date)
            logger.info(
                f"{prefix} — analysis complete: "
                f"work_type='{analysis.work_type}' "
                f"is_call_ok={analysis.is_call_ok} "
                f"score={analysis.score}"
            )

            # ── d. Upload transcript back to Drive ─────────────────────
            txt_name = Path(file_name).stem + ".txt"
            logger.info(f"{prefix} — uploading transcript as '{txt_name}' ...")
            drive.upload_text_file(
                folder_id=settings.source_drive_folder_id,
                file_name=txt_name,
                text_content=transcript,
            )
            logger.info(f"{prefix} — transcript uploaded")

            # ── e. Write result row (+ conditional red formatting) ─────
            logger.info(f"{prefix} — appending result to spreadsheet ...")
            sheets.append_analysis_result(
                spreadsheet_id=spreadsheet_id,
                sheet_name=settings.output_sheet_name,
                sheet_id=settings.output_sheet_id,
                date_str=today,
                analysis=analysis,
                call_type=call_type,
                phone_number=phone_number,
            )
            logger.info(f"{prefix} — row written to spreadsheet")

            processed += 1
            logger.info(f"{prefix} — DONE")

        except (TranscriptionError, AnalysisError, SheetsServiceError) as exc:
            # Known, named service-level failures — the message is self-explanatory.
            logger.error(f"{prefix} — service error: {exc}")
            failed += 1

        except Exception as exc:
            # Unexpected failure — include full traceback so it can be diagnosed.
            logger.opt(exception=True).error(
                f"{prefix} — unexpected error: {exc}"
            )
            failed += 1

        finally:
            # Always remove the local audio file to keep disk usage bounded,
            # regardless of whether the rest of the pipeline succeeded or failed.
            if local_path is not None and local_path.exists():
                local_path.unlink()
                logger.debug(f"{prefix} — local file deleted")

    # ------------------------------------------------------------------
    # 5. Summary
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info(
        f"Pipeline complete — "
        f"{processed}/{total} succeeded, {failed}/{total} failed"
    )
    if processed:
        logger.info(f"Report spreadsheet: {spreadsheet_id}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
