"""Tests for GoogleDriveService.

Strategy
--------
- ``_load_oauth_credentials`` is patched at the module level in the
  ``drive_service`` fixture, completely bypassing all auth logic for the
  method-behaviour tests.
- Credential-loading tests call ``_load_oauth_credentials`` directly and patch
  only its internal dependencies (``Credentials``, ``InstalledAppFlow``,
  ``Request``) at the module-level import path so the real code paths execute.
- Every test that exercises a Drive method receives the same ``MagicMock``
  that ``build`` was made to return, so assertions can be made on the exact
  call-chain the service issued.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_mock

from app.services.google_drive import (
    CredentialsNotFoundError,
    GoogleDriveService,
    _load_oauth_credentials,
    _SCOPES,
)

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------
FOLDER_ID = "test-folder-id-abc"
FILE_ID = "test-file-id-xyz"
FILE_NAME = "recording.mp3"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_mock() -> MagicMock:
    """The MagicMock returned by ``build()``.  Tests configure responses on it."""
    return MagicMock()


@pytest.fixture()
def drive_service(
    api_mock: MagicMock,
    mocker: pytest_mock.MockerFixture,
) -> GoogleDriveService:
    """A fully-mocked GoogleDriveService — auth is bypassed entirely."""
    mocker.patch(
        "app.services.google_drive._load_oauth_credentials",
        return_value=MagicMock(),
    )
    mocker.patch("app.services.google_drive.build", return_value=api_mock)
    return GoogleDriveService()


# ---------------------------------------------------------------------------
# Credentials loading  (_load_oauth_credentials tested directly)
# ---------------------------------------------------------------------------

class TestCredentialsLoading:
    def test_raises_when_no_token_and_no_credentials_file(
        self, tmp_path: Path
    ) -> None:
        """Both token.json and credentials.json absent → CredentialsNotFoundError."""
        with pytest.raises(CredentialsNotFoundError, match="credentials.json"):
            _load_oauth_credentials(
                credentials_path=tmp_path / "credentials.json",
                token_path=tmp_path / "token.json",
                scopes=_SCOPES,
            )

    def test_error_message_contains_credentials_path(
        self, tmp_path: Path
    ) -> None:
        creds_path = tmp_path / "credentials.json"
        with pytest.raises(CredentialsNotFoundError) as exc_info:
            _load_oauth_credentials(
                credentials_path=creds_path,
                token_path=tmp_path / "token.json",
                scopes=_SCOPES,
            )
        assert str(creds_path) in str(exc_info.value)

    def test_returns_credentials_from_valid_token_file(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture
    ) -> None:
        """If token.json exists and is valid, return it without running the flow."""
        token_file = tmp_path / "token.json"
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = True
        mocker.patch(
            "app.services.google_drive.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        )

        result = _load_oauth_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token_file,
            scopes=_SCOPES,
        )

        assert result is mock_creds

    def test_refreshes_expired_token_silently(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture
    ) -> None:
        """Expired token with a refresh_token is refreshed without a browser."""
        token_file = tmp_path / "token.json"
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "rtoken"
        mock_creds.to_json.return_value = "{}"
        mocker.patch(
            "app.services.google_drive.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        )
        mock_request_cls = mocker.patch("app.services.google_drive.Request")

        _load_oauth_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token_file,
            scopes=_SCOPES,
        )

        mock_creds.refresh.assert_called_once_with(mock_request_cls.return_value)

    def test_runs_installed_app_flow_when_no_token(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture
    ) -> None:
        """No token.json → InstalledAppFlow opened with port=0."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}")

        mock_new_creds = MagicMock()
        mock_new_creds.to_json.return_value = "{}"
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_new_creds
        mock_flow_cls = mocker.patch(
            "app.services.google_drive.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        )

        result = _load_oauth_credentials(
            credentials_path=creds_file,
            token_path=tmp_path / "token.json",
            scopes=_SCOPES,
        )

        mock_flow_cls.assert_called_once_with(str(creds_file), _SCOPES)
        mock_flow.run_local_server.assert_called_once_with(port=0)
        assert result is mock_new_creds

    def test_saves_token_after_new_auth(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture
    ) -> None:
        """Newly obtained credentials are persisted to token_path."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}")
        token_file = tmp_path / "token.json"

        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"access_token": "new"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mocker.patch(
            "app.services.google_drive.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        )

        _load_oauth_credentials(
            credentials_path=creds_file,
            token_path=token_file,
            scopes=_SCOPES,
        )

        assert token_file.exists()
        assert '"access_token"' in token_file.read_text()


# ---------------------------------------------------------------------------
# get_audio_files_metadata
# ---------------------------------------------------------------------------

class TestGetAudioFilesMetadata:
    def test_returns_list_from_api(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        expected = [
            {"id": "id1", "name": "call1.mp3", "mimeType": "audio/mpeg"},
            {"id": "id2", "name": "call2.ogg", "mimeType": "audio/ogg"},
        ]
        api_mock.files.return_value.list.return_value.execute.return_value = {
            "files": expected
        }

        result = drive_service.get_audio_files_metadata(FOLDER_ID)

        assert result == expected

    def test_returns_empty_list_when_folder_is_empty(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.list.return_value.execute.return_value = {"files": []}

        result = drive_service.get_audio_files_metadata(FOLDER_ID)

        assert result == []

    def test_returns_empty_list_when_key_absent_in_response(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        """API can omit the 'files' key entirely when there are no results."""
        api_mock.files.return_value.list.return_value.execute.return_value = {}

        result = drive_service.get_audio_files_metadata(FOLDER_ID)

        assert result == []

    def test_query_contains_folder_id(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.list.return_value.execute.return_value = {"files": []}

        drive_service.get_audio_files_metadata(FOLDER_ID)

        query: str = api_mock.files.return_value.list.call_args.kwargs["q"]
        assert FOLDER_ID in query

    def test_query_filters_by_audio_mime_type(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.list.return_value.execute.return_value = {"files": []}

        drive_service.get_audio_files_metadata(FOLDER_ID)

        query: str = api_mock.files.return_value.list.call_args.kwargs["q"]
        assert "audio" in query

    def test_query_excludes_trashed_files(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.list.return_value.execute.return_value = {"files": []}

        drive_service.get_audio_files_metadata(FOLDER_ID)

        query: str = api_mock.files.return_value.list.call_args.kwargs["q"]
        assert "trashed=false" in query


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

class TestDownloadFile:
    def _patch_downloader(
        self,
        mocker: pytest_mock.MockerFixture,
        *,
        chunks: list[tuple],
    ) -> MagicMock:
        """Return a patched MediaIoBaseDownload whose next_chunk follows *chunks*."""
        mock_dl = MagicMock()
        mock_dl.next_chunk.side_effect = chunks
        mocker.patch("app.services.google_drive.MediaIoBaseDownload", return_value=mock_dl)
        return mock_dl

    def test_returns_destination_path(
        self,
        drive_service: GoogleDriveService,
        api_mock: MagicMock,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        self._patch_downloader(mocker, chunks=[(None, True)])

        result = drive_service.download_file(FILE_ID, FILE_NAME, tmp_path)

        assert result == tmp_path / FILE_NAME

    def test_creates_nested_download_directory(
        self,
        drive_service: GoogleDriveService,
        api_mock: MagicMock,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        nested = tmp_path / "level1" / "level2"
        self._patch_downloader(mocker, chunks=[(None, True)])

        drive_service.download_file(FILE_ID, FILE_NAME, nested)

        assert nested.is_dir()

    def test_calls_get_media_with_file_id(
        self,
        drive_service: GoogleDriveService,
        api_mock: MagicMock,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        self._patch_downloader(mocker, chunks=[(None, True)])

        drive_service.download_file(FILE_ID, FILE_NAME, tmp_path)

        api_mock.files.return_value.get_media.assert_called_once_with(fileId=FILE_ID)

    def test_loops_until_done(
        self,
        drive_service: GoogleDriveService,
        api_mock: MagicMock,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        """next_chunk must be called until done=True, not just once."""
        mock_dl = self._patch_downloader(
            mocker,
            chunks=[(None, False), (None, False), (None, True)],
        )

        drive_service.download_file(FILE_ID, FILE_NAME, tmp_path)

        assert mock_dl.next_chunk.call_count == 3


# ---------------------------------------------------------------------------
# upload_text_file
# ---------------------------------------------------------------------------

class TestUploadTextFile:
    def test_returns_new_file_id(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.create.return_value.execute.return_value = {
            "id": "new-drive-file-id"
        }

        result = drive_service.upload_text_file(FOLDER_ID, "transcript.txt", "Hello world.")

        assert result == "new-drive-file-id"

    def test_sets_correct_parent_folder_in_metadata(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.create.return_value.execute.return_value = {"id": "x"}

        drive_service.upload_text_file(FOLDER_ID, "transcript.txt", "content")

        body: dict = api_mock.files.return_value.create.call_args.kwargs["body"]
        assert body["parents"] == [FOLDER_ID]

    def test_sets_correct_file_name_in_metadata(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.create.return_value.execute.return_value = {"id": "x"}

        drive_service.upload_text_file(FOLDER_ID, "my_transcript.txt", "content")

        body: dict = api_mock.files.return_value.create.call_args.kwargs["body"]
        assert body["name"] == "my_transcript.txt"

    def test_requests_id_field_in_response(
        self, drive_service: GoogleDriveService, api_mock: MagicMock
    ) -> None:
        api_mock.files.return_value.create.return_value.execute.return_value = {"id": "x"}

        drive_service.upload_text_file(FOLDER_ID, "transcript.txt", "content")

        assert (
            api_mock.files.return_value.create.call_args.kwargs["fields"] == "id"
        )
