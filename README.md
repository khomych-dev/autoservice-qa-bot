# Autoservice QA Bot

An automated pipeline that audits phone-call quality at a Ukrainian auto service center.  
It pulls audio recordings from **Google Drive**, transcribes them locally with **OpenAI Whisper**, evaluates manager performance with **GPT-4o-mini Structured Outputs**, and writes the results — including automatic red-cell highlighting and solid-border row formatting — into a **Google Sheets** report copied from a master template.

---

## How it works

```
Google Drive folder  (audio files)
        │
        ▼
  Parse filename
  (call type, phone number, call date)
        │
        ▼
  Download audio  →  data/  (temp)
        │
        ▼
  OpenAI Whisper (whisper-1)
  transcription
        │
        ▼
  GPT-4o-mini structured analysis
  (Pydantic / JSON Schema Structured Outputs)
        │
        ├──▶  Upload transcript (.txt) back to Drive
        │
        └──▶  Append 20-column row to Google Sheets report
               • red bg + white bold text on col 19 (Коментар) for bad calls
               • explicit white bg reset on col 19 for good calls
               • solid black borders across all 20 columns of every new row
```

Each run creates a fresh copy of your Sheets template titled **`QA Report — YYYY-MM-DD`**.  
If a single file fails (network error, API error, etc.) the pipeline logs the error and **continues with the next file** — the batch is never aborted mid-run.

---

## Key features

| Feature | Detail |
|---|---|
| **Google Drive integration** | Lists audio files, downloads them to `data/` (auto-deleted after processing), uploads plain-text transcripts back |
| **Whisper transcription** | `whisper-1` via the OpenAI API; handles any audio format FFmpeg supports |
| **GPT-4o-mini Structured Outputs** | `CallAnalysis` Pydantic model enforced as a JSON Schema — the model **cannot** return invalid `work_type`, `result`, or `spare_parts` values |
| **Strict enum / Literal validation** | `WorkType` is a `Literal` of ~100 exact Ukrainian service-work strings mirroring the Sheets data-validation dropdown; `result` and `spare_parts` are also strict `Literal` types |
| **20-column Sheets export** | Each row maps to the Ukrainian-language template columns (Date, Call type, Phone, Greeting, Car body, Car year, Mileage, Diagnostics, Previous works, Appointment date, Goodbye, Work type, Is OK, Result, Score, Spare parts, Comment, …) |
| **Dynamic cell formatting** | Bad calls → col 19 red background + white bold text; good calls → explicit white reset (prevents Google Sheets `INSERT_ROWS` colour inheritance); all rows → solid black borders via a single `batchUpdate` |
| **Filename parsing** | Extracts call direction (Вхідний / Вихідний), Ukrainian phone number (`380XXXXXXXXX` / `0XXXXXXXXX`), and call date (ISO or European format) from the audio filename |
| **Error isolation** | Known service errors caught per-file; unexpected exceptions logged with full traceback — pipeline always advances to the next file |

---

## Tech stack

| Component | Library / Service |
|---|---|
| Language | Python 3.12 |
| Dependency manager | [uv](https://docs.astral.sh/uv) |
| Transcription | OpenAI `whisper-1` (`openai >= 2.37`) |
| LLM analysis | OpenAI `gpt-4o-mini` Structured Outputs |
| Schema validation | Pydantic v2 + `pydantic-settings` |
| Google Drive API | `google-api-python-client` v3 |
| Google Sheets API | `google-api-python-client` v4 |
| OAuth 2.0 | `google-auth-oauthlib` (Desktop App flow) |
| Logging | `loguru` |
| Tests | `pytest` + `pytest-mock` (all external calls mocked) |

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| **Python** | 3.12 or later |
| **FFmpeg** | Must be on `PATH` — required by Whisper for audio file handling |
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
> **Scope note:** both the Drive and Sheets services require `drive` + `spreadsheets` scopes.  
> If you see a scope mismatch error, delete `token.json` and re-authenticate.

---

## Running the pipeline

```bash
python -m app.main
```

The bot will:

1. Initialise all four services (fails immediately if credentials or env vars are missing).
2. Create today's report spreadsheet from the template (`QA Report — YYYY-MM-DD`).
3. List all audio files in the configured Drive folder.
4. Process each file in sequence:
   - Parse the filename for call direction, phone number, and call date.
   - Download the audio to `data/`.
   - Transcribe via Whisper.
   - Analyse with GPT-4o-mini (Structured Outputs → `CallAnalysis`).
   - Upload the plain-text transcript back to Drive.
   - Append the 20-column results row to the report, with automatic cell formatting.
   - Delete the local audio file immediately.
5. Print a summary (`X/Y succeeded, Z/Y failed`).

---

## Google Sheets column mapping

The appended row is exactly **20 columns wide** and maps to the Ukrainian-language template headers:

| Col | Header (Ukrainian) | Source |
|:---:|---|---|
| A (0) | Дата | call date parsed from filename |
| B (1) | Тип звернення | `Вхідний` / `Вихідний` / `""` — parsed from filename |
| C (2) | Номер телефону | phone number parsed from filename |
| D (3) | Філія | *(left empty)* |
| E (4) | Менеджер | *(left empty)* |
| F (5) | Початок розмови, представлення | `analysis.greeting_start` (0/1) |
| G (6) | Чи дізнався менеджер кузов | `analysis.asked_car_body` (0/1) |
| H (7) | Чи дізнався менеджер рік | `analysis.asked_car_year` (0/1) |
| I (8) | Чи дізнався менеджер пробіг | `analysis.asked_mileage` (0/1) |
| J (9) | Пропозиція про комплексну діагностику | `analysis.offered_diagnostics` (0/1) |
| K (10) | Дізнався які роботи робилися | `analysis.asked_previous_works` (0/1) |
| L (11) | Запис на сервіс, Дата | `analysis.appointment_date` (`"0"` when none) |
| M (12) | Завершення розмови, прощання | `analysis.goodbye_end` (0/1) |
| N (13) | Яка робота з топ 100 | `analysis.work_type` (strict Literal enum) |
| O (14) | Чи дотримувався всіх інструкцій | `int(analysis.is_call_ok)` (1/0) |
| P (15) | Яких рекомендацій не дотримувався | *(left empty)* |
| Q (16) | Результат | `analysis.result` (strict Literal enum) |
| R (17) | Оцінка | `analysis.score` |
| S (18) | Запчастини | `analysis.spare_parts` (strict Literal enum) |
| T (19) | Коментар | `analysis.red_flag_comment` — **red bg / white bold** on bad calls; explicit white reset on good calls |

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
│   │   └── schemas.py           # CallAnalysis Pydantic model + WorkType Literal
│   └── services/
│       ├── google_drive.py      # Drive: list / download / upload
│       ├── google_sheets.py     # Sheets: copy template / append / format
│       ├── transcription.py     # OpenAI Whisper transcription
│       └── analyzer.py          # GPT-4o-mini structured analysis
├── tests/                       # pytest unit tests (130 tests, all mocked)
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

All 130 tests run without real API calls — every external dependency (OpenAI, Google Drive, Google Sheets, filesystem) is fully mocked with `pytest-mock`.

To run a specific test module:

```bash
uv run pytest tests/test_analyzer.py
uv run pytest tests/test_google_sheets.py
uv run pytest tests/test_main.py
```
