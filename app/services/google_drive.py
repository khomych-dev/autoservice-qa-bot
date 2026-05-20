from __future__ import annotations

import io
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from app.core.logger import logger

_SCOPES: list[str] = ["https://www.googleapis.com/auth/drive"]
_AUDIO_QUERY_FRAGMENT = "mimeType contains 'audio'"


class CredentialsNotFoundError(FileNotFoundError):
    """Raised when the service-account credentials file cannot be found."""


class GoogleDriveService:
    """Thin wrapper around the Google Drive v3 API.

    Responsibilities (SRP):
        - load credentials and build the API client once at construction time
        - expose focused, named methods for each Drive operation the pipeline needs

    Dependency-Inversion note:
        `credentials_path` and the `build` function are injected (or patchable)
        so that tests never touch the network or the filesystem for credentials.
    """

    def __init__(
        self,
        credentials_path: Path = Path("credentials.json"),
    ) -> None:
        credentials = _load_service_account_credentials(credentials_path)
        self._service = build("drive", "v3", credentials=credentials)
        logger.info("GoogleDriveService initialised.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_audio_files_metadata(self, folder_id: str) -> list[dict]:
        """Return metadata for every non-trashed audio file in *folder_id*.

        Fields returned per file: id, name, mimeType, size, createdTime.
        """
        query = (
            f"'{folder_id}' in parents "
            f"and {_AUDIO_QUERY_FRAGMENT} "
            "and trashed=false"
        )
        logger.info(f"Querying audio files in Drive folder '{folder_id}'.")
        response: dict = (
            self._service.files()
            .list(
                q=query,
                fields="files(id, name, mimeType, size, createdTime)",
                pageSize=1000,
            )
            .execute()
        )
        files: list[dict] = response.get("files", [])
        logger.info(f"Found {len(files)} audio file(s) in folder '{folder_id}'.")
        return files

    def download_file(
        self,
        file_id: str,
        file_name: str,
        download_path: Path,
    ) -> Path:
        """Download *file_id* from Drive and save it to *download_path / file_name*.

        Returns the absolute path of the written file.
        Creates *download_path* (including parents) if it does not exist.
        """
        download_path.mkdir(parents=True, exist_ok=True)
        destination = download_path / file_name

        request = self._service.files().get_media(fileId=file_id)
        logger.info(f"Downloading '{file_name}' (id={file_id}) → {destination}")

        with destination.open("wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.debug(f"  '{file_name}' — {pct}% complete")

        logger.info(f"Download complete: '{file_name}'.")
        return destination

    def upload_text_file(
        self,
        folder_id: str,
        file_name: str,
        text_content: str,
    ) -> str:
        """Upload *text_content* as a plain-text file into *folder_id*.

        Returns the Drive file-id of the newly created file.
        """
        logger.info(f"Uploading '{file_name}' to Drive folder '{folder_id}'.")
        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(text_content.encode("utf-8")),
            mimetype="text/plain",
            resumable=False,
        )
        response: dict = (
            self._service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        new_file_id: str = response["id"]
        logger.info(f"Uploaded '{file_name}' — Drive file id: {new_file_id}")
        return new_file_id


# ------------------------------------------------------------------
# Module-level helper (keeps the class constructor thin and mockable)
# ------------------------------------------------------------------

def _load_service_account_credentials(
    credentials_path: Path,
) -> service_account.Credentials:
    if not credentials_path.exists():
        raise CredentialsNotFoundError(
            f"Google API credentials file not found at '{credentials_path}'. "
            "Download the service-account JSON from the Google Cloud Console "
            "and place it at that path."
        )
    logger.debug(f"Loading service-account credentials from '{credentials_path}'.")
    return service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=_SCOPES,
    )
