from app.core.logger import logger


def main() -> None:
    logger.info("Autoservice QA Bot starting up...")
    # Pipeline steps will be orchestrated here:
    # 1. Download audio files from Google Drive
    # 2. Transcribe audio via Whisper
    # 3. Analyze transcription via OpenAI
    # 4. Write results to Google Sheets


if __name__ == "__main__":
    main()
