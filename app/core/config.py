from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # --- Required ---
    openai_api_key: str
    source_drive_folder_id: str
    template_spreadsheet_id: str

    # --- Optional (override in .env when the template uses different values) ---
    output_sheet_name: str = "Sheet1"
    output_sheet_id: int = 0


settings = Settings()
