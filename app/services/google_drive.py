from __future__ import annotations

import io
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from app.core.logger import logger

_SCOPES: list[str] = ["https://www.googleapis.com/auth/drive"]
_AUDIO_QUERY_FRAGMENT = "mimeType contains 'audio'"


class CredentialsNotFoundError(FileNotFoundError):
    """Raised when credentials.json is absent and no cached token.json exists."""


class GoogleDriveService:
    """Thin wrapper around the Google Drive v3 API.

    Responsibilities (SRP):
        - load / refresh OAuth credentials and build the API client once at
          construction time
        - expose focused, named methods for each Drive operation the pipeline needs

    Dependency-Inversion note:
        ``credentials_path``, ``token_path``, and the ``build`` function are all
        injectable so tests can bypass authentication entirely by patching
        ``_load_oauth_credentials`` at the module level.
    """

    def __init__(
        self,
        credentials_path: Path = Path("credentials.json"),
        token_path: Path = Path("token.json"),
    ) -> None:
        credentials = _load_oauth_credentials(credentials_path, token_path, _SCOPES)
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
