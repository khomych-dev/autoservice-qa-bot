import os

# Guard against the module-level `settings = Settings()` in app.core.config
# raising a ValidationError during pytest collection, when .env is empty and
# no real env vars are present.  setdefault leaves any genuine values intact.
os.environ.setdefault("OPENAI_API_KEY", "_pytest_placeholder_")
os.environ.setdefault("SOURCE_DRIVE_FOLDER_ID", "_pytest_placeholder_")
