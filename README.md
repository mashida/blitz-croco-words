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

### Environment
Create `.env` in the repo root (or use `.env.example`) and fill in:
- `APP_USER` (default: `admin`)
- `APP_PASSWORD` (default: `admin`)
- `APP_DB_PATH` (default: `data/words.db`)

### Run
1. Install deps: `uv sync`
2. Start server: `uv run croco`
3. Dev mode (auto-reload): `uv run croco --dev`
4. Custom host/port: `uv run croco --host 0.0.0.0 --port 8000`

`.env` is loaded automatically if present; environment variables from the
runtime still take precedence.

Open `http://localhost:8000` and sign in with Basic Auth.

### Authentication
UI uses a login form on `/login` (cookie-based session).
API requests still use Basic Auth.
Set admin credentials via env vars:
- `APP_USER` (default: `admin`)
- `APP_PASSWORD` (default: `admin`)

### Admin UI
Use `/admin` to manage users (create, update passwords, roles, delete).
Only the admin user can access it. The admin user is created from
`APP_USER` / `APP_PASSWORD` on first start; changing `.env` later does not
update the stored password in the database.

### Storage
SQLite database path:
- `APP_DB_PATH` (default: `data/words.db`)

### UI
- Upload: multiple `.pptx` or a `.zip` containing `.pptx`
- Download: `words.txt` with `n` least-recently-used words for the user
- Browse words: `/words` (paginated list with sorting)

### API
- `POST /api/upload` (multipart form field `file`)
- `GET /api/words?n=120`
- `POST /api/users` JSON: `{"username": "...", "password": "..."}`

### Docker
Build and run locally:
1. `docker build -t blitz-croco-words .`
2. `docker run -p 8000:8000 -e APP_USER=admin -e APP_PASSWORD=admin blitz-croco-words`

### GitLab CI/CD
- Merge requests: `ruff`, `pylint`, and `pytest`
- `main` branch: Docker build and deploy with shell executor
- Configure runner variables: `APP_USER`, `APP_PASSWORD`, `APP_PORT`, `APP_DATA_DIR`