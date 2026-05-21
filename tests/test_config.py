import pytest
from pydantic import ValidationError

from app.core.config import Settings


class TestSettingsValidValues:
    def test_reads_both_fields_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-key-abc123")
        monkeypatch.setenv("SOURCE_DRIVE_FOLDER_ID", "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs")

        s = Settings()

        assert s.openai_api_key == "sk-test-openai-key-abc123"
        assert s.source_drive_folder_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"


class TestSettingsFailFast:
    def test_raises_when_all_fields_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("SOURCE_DRIVE_FOLDER_ID", raising=False)
        monkeypatch.delenv("TEMPLATE_SPREADSHEET_ID", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            # _env_file=None prevents pydantic-settings from reading the real
            # .env file on disk, which would otherwise satisfy the missing fields.
            Settings(_env_file=None)

        missing = {err["loc"][0] for err in exc_info.value.errors()}
        assert "openai_api_key" in missing
        assert "source_drive_folder_id" in missing

    def test_raises_when_single_field_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-key-abc123")
        monkeypatch.setenv("TEMPLATE_SPREADSHEET_ID", "tmpl-id")
        monkeypatch.delenv("SOURCE_DRIVE_FOLDER_ID", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)

        missing = {err["loc"][0] for err in exc_info.value.errors()}
        assert "source_drive_folder_id" in missing
        assert "openai_api_key" not in missing
