"""Tests for GoogleDriveService.

Strategy
--------
- ``build`` (googleapiclient.discovery) and
  ``service_account.Credentials.from_service_account_file`` are patched at the
  module-level import path used by the service (``app.services.google_drive.*``).
- A real (but empty) ``credentials.json`` is written to pytest's ``tmp_path``
  so that ``Path.exists()`` passes without any monkey-patching of the stdlib.
- Every test that exercises a Drive method receives the same ``MagicMock``
  that ``build`` was made to return, so assertions can be made on the exact
  call-chain the service issued.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_mock

from app.services.google_drive import CredentialsNotFoundError, GoogleDriveService

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
def fake_credentials_file(tmp_path: Path) -> Path:
    """Write a placeholder credentials.json so Path.exists() returns True."""
    p = tmp_path / "credentials.json"
    p.write_text('{"type": "service_account"}')
    return p


@pytest.fixture()
def api_mock() -> MagicMock:
    """The MagicMock returned by ``build()``.  Tests configure responses on it."""
    return MagicMock()


@pytest.fixture()
def drive_service(
    fake_credentials_file: Path,
    api_mock: MagicMock,
    mocker: pytest_mock.MockerFixture,
) -> GoogleDriveService:
    """A fully-mocked GoogleDriveService ready for unit testing."""
    mocker.patch(
        "app.services.google_drive.service_account.Credentials.from_service_account_file",
        return_value=MagicMock(),
    )
    mocker.patch("app.services.google_drive.build", return_value=api_mock)
    return GoogleDriveService(credentials_path=fake_credentials_file)


# ---------------------------------------------------------------------------
# Credentials loading
# ---------------------------------------------------------------------------

class TestCredentialsLoading:
    def test_raises_when_file_is_absent(self) -> None:
        missing = Path("/no/such/path/credentials.json")
        with pytest.raises(CredentialsNotFoundError, match="credentials.json"):
            GoogleDriveService(credentials_path=missing)

    def test_error_message_contains_path(self) -> None:
        missing = Path("/no/such/path/credentials.json")
        with pytest.raises(CredentialsNotFoundError) as exc_info:
            GoogleDriveService(credentials_path=missing)
        assert str(missing) in str(exc_info.value)

    def test_calls_from_service_account_file_with_correct_path(
        self,
        fake_credentials_file: Path,
        api_mock: MagicMock,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mock_loader = mocker.patch(
            "app.services.google_drive.service_account.Credentials.from_service_account_file",
            return_value=MagicMock(),
        )
        mocker.patch("app.services.google_drive.build", return_value=api_mock)
        GoogleDriveService(credentials_path=fake_credentials_file)
        mock_loader.assert_called_once_with(
            str(fake_credentials_file), scopes=["https://www.googleapis.com/auth/drive"]
        )


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
