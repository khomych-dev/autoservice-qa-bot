# Autoservice QA Bot

An automated pipeline that audits phone-call quality at an auto service center.
It pulls audio recordings from **Google Drive**, transcribes them with **OpenAI Whisper**, evaluates manager performance with **GPT-4o-mini Structured Outputs**, and writes the results — including automatic red-cell highlighting for failed calls — into a **Google Sheets** report copied from a template.

---

## How it works

```
Google Drive folder
       │
       ▼
  Download audio
       │
       ▼
 Whisper (whisper-1)
  transcription
       │
       ▼
GPT-4o-mini structured
    analysis
       │
       ├──▶ Upload transcript (.txt) back to Drive
       │
       └──▶ Append row to Google Sheets report
              (red cell on column F for bad calls)
```

Each run creates a fresh copy of your Sheets template titled **`QA Report — YYYY-MM-DD`**.
For every audio file the pipeline appends one row with these seven columns:

| A | B | C | D | E | F | G |
|---|---|---|---|---|---|---|
| Date | Has Recording | Work Type | Manager Evaluation | Is Call OK | Red Flag Comment | Score |

If `Is Call OK` is `False`, the **Red Flag Comment** cell is automatically formatted with a red background and white bold text.

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| **Python** | 3.12 or later |
| **FFmpeg** | Must be on `PATH` — required for audio file handling |
| **uv** | Dependency manager — install from [docs.astral.sh/uv](https://docs.astral.sh/uv) |
| **Google Cloud project** | OAuth 2.0 Desktop App credentials (`credentials.json`) |
| **OpenAI account** | API key with access to `whisper-1` and `gpt-4o-mini` |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/khomych-dev/autoservice-qa-bot.git
cd autoservice-qa-bot

# 2. Create a virtual environment and install all dependencies
uv sync

# 3. Activate the virtual environment
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

---

## Configuration

### 1. Environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set the following:

```env
# OpenAI
OPENAI_API_KEY=sk-...                         # Your OpenAI API key

# Google Drive
SOURCE_DRIVE_FOLDER_ID=<folder_id>            # ID of the Drive folder containing audio files

# Google Sheets
TEMPLATE_SPREADSHEET_ID=<spreadsheet_id>      # ID of the Sheets file used as the report template
```

> **Finding IDs**
> - **Drive folder ID** — open the folder in Google Drive; the last segment of the URL is the ID.
> - **Spreadsheet ID** — open the sheet; it is the long alphanumeric string between `/d/` and `/edit` in the URL.

Two optional variables control which sheet tab the bot writes to (defaults match a freshly created spreadsheet):

```env
# Optional — only needed if your template uses non-default values
OUTPUT_SHEET_NAME=Sheet1          # Name of the tab inside the spreadsheet
OUTPUT_SHEET_ID=1558940291        # Numeric gid of the tab (visible as gid= in the URL)
```

### 2. Google OAuth credentials (`credentials.json`)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create (or open) a project and enable the **Google Drive API** and **Google Sheets API**.
3. Under **APIs & Services → Credentials**, create an **OAuth 2.0 Client ID** of type **Desktop App**.
4. Download the JSON file and place it in the project root, named exactly `credentials.json`.

> `credentials.json` is listed in `.gitignore` and will never be committed.

### 3. OAuth token (`token.json`)

`token.json` is generated **automatically on first run**. When no valid token is found, the bot opens a browser window to complete the OAuth Desktop flow. After you grant access, the token is saved to `token.json` in the project root and reused on all subsequent runs (silently refreshed when expired).

> `token.json` is also listed in `.gitignore`.
>
> **Scope note:** the Drive service uses the `drive` scope; the Sheets service uses
> `spreadsheets` + `drive`. If you see a scope mismatch error, delete `token.json`
> and re-authenticate.

---

## Running the pipeline

```bash
python -m app.main
```

The bot will:

1. Initialise all four services (fails immediately if credentials or env vars are missing).
2. Create today's report spreadsheet from the template.
3. List all audio files in the configured Drive folder.
4. Process each file in sequence — transcribe, analyse, upload the transcript, append the results row.
5. Print a summary (`X/Y succeeded, Z/Y failed`).

If a single file fails (network error, API error, etc.) the pipeline logs the error and **continues with the next file** — the run is never aborted mid-batch.

Downloaded audio files are stored temporarily in the `data/` directory and deleted immediately after each file is processed.

---

## Project structure

```
autoservice-qa-bot/
├── app/
│   ├── main.py                  # Pipeline entry point
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (reads .env)
│   │   └── logger.py            # Loguru stderr logger
│   ├── models/
│   │   └── schemas.py           # CallAnalysis Pydantic model
│   └── services/
│       ├── google_drive.py      # Drive: list / download / upload
│       ├── google_sheets.py     # Sheets: copy template / append / format
│       ├── transcription.py     # OpenAI Whisper transcription
│       └── analyzer.py          # GPT-4o-mini structured analysis
├── tests/                       # pytest unit tests (85 tests, all mocked)
├── data/                        # Temporary audio downloads (gitignored)
├── credentials.json             # OAuth Desktop App credentials (gitignored)
├── token.json                   # OAuth token — auto-generated (gitignored)
├── .env                         # Local environment variables (gitignored)
├── .env.example                 # Template for .env
└── pyproject.toml               # Project metadata and dependencies (uv)
```

---

## Running tests

```bash
uv run pytest
```

All 85 tests run without real API calls — every external dependency is mocked.
