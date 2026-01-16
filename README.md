# blitz-croco-words

Extracts single-word clues from pptx slides inside a zip archive, runs Yandex
Speller, and writes the normalized words to a text file.

## Requirements
- Python >= 3.10
- uv (https://github.com/astral-sh/uv)

## Setup
1. uv sync

## Usage
Default archive path: `src/croco-blitz-source.zip` (relative to the repo root).
Default output path: `words.txt` (repo root).

uv run python main.py --archive src/croco-blitz-source.zip --output words.txt

Notes:
- Only `.pptx` files are processed from the zip archive.
- The Yandex Speller call requires network access.

## Web server
The web server exposes a minimal UI and a JSON API with Basic Auth.

### Run
1. Install deps: `uv sync`
2. Start server: `uv run uvicorn server:app --host 0.0.0.0 --port 8000`

Open `http://localhost:8000` and sign in with Basic Auth.

### Authentication
Set credentials via env vars:
- `APP_USER` (default: `admin`)
- `APP_PASSWORD` (default: `admin`)

### Storage
SQLite database path:
- `APP_DB_PATH` (default: `data/words.db`)

### UI
- Upload: `.pptx` or `.zip` containing `.pptx`
- Download: `words.txt` with `n` least-recently-used words for the user

### API
- `POST /api/upload` (multipart form field `file`)
- `GET /api/words?n=120`
- `POST /api/users` JSON: `{"username": "...", "password": "..."}`