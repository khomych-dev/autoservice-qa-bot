from __future__ import annotations

import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logger import logger
from app.services.analyzer import AnalysisError, AnalyzerService
from app.services.google_drive import GoogleDriveService
from app.services.google_sheets import GoogleSheetsService, SheetsServiceError
from app.services.transcription import TranscriptionError, TranscriptionService

_DATA_DIR = Path("data")

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
            analysis = analyzer.analyze_transcript(transcript)
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
