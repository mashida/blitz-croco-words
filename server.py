from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from helpers import check_spelling, get_words_form_file

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "words.db"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"
PASSWORD_ITERATIONS = 200_000
DEFAULT_WORD_COUNT = 120
MAX_WORD_COUNT = 10_000

security = HTTPBasic()
app = FastAPI()


@dataclass(frozen=True)
class User:
    id: int
    username: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    raw_path = os.getenv("APP_DB_PATH", str(DEFAULT_DB_PATH))
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_word_usage (
                user_id INTEGER NOT NULL,
                word_id INTEGER NOT NULL,
                last_used_at TEXT NOT NULL,
                PRIMARY KEY (user_id, word_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
            );
            """
        )


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected)


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, username, password_salt, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()


def create_user(conn: sqlite3.Connection, username: str, password: str) -> int:
    salt = secrets.token_bytes(16)
    password_hash = hash_password(password, salt)
    conn.execute(
        """
        INSERT INTO users (username, password_salt, password_hash, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (username, salt, password_hash, utc_now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to create user.")
    return int(row["id"])


def ensure_default_user(db_path: Path) -> None:
    username = os.getenv("APP_USER", DEFAULT_USER)
    password = os.getenv("APP_PASSWORD", DEFAULT_PASSWORD)
    if "APP_USER" not in os.environ or "APP_PASSWORD" not in os.environ:
        logging.warning(
            "APP_USER or APP_PASSWORD not set. Using default credentials."
        )
    with open_connection(db_path) as conn:
        existing = get_user_by_username(conn, username)
        if existing is None:
            create_user(conn, username, password)


def get_db() -> sqlite3.Connection:
    db_path = get_db_path()
    conn = open_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    credentials: HTTPBasicCredentials = Depends(security),
    conn: sqlite3.Connection = Depends(get_db),
) -> User:
    row = get_user_by_username(conn, credentials.username)
    if row is None or not verify_password(
        credentials.password, row["password_salt"], row["password_hash"]
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return User(id=int(row["id"]), username=str(row["username"]))


def extract_words_from_zip(zip_bytes: bytes) -> list[str]:
    words: list[str] = []
    try:
        with ZipFile(BytesIO(zip_bytes)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if Path(info.filename).suffix.lower() != ".pptx":
                    continue
                with archive.open(info) as file:
                    words.extend(get_words_form_file(file))
    except BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc
    return words


def extract_words_from_upload(upload: UploadFile) -> list[str]:
    filename = upload.filename or "upload"
    suffix = Path(filename).suffix.lower()
    upload.file.seek(0)
    if suffix == ".pptx":
        return get_words_form_file(upload.file)
    if suffix == ".zip":
        return extract_words_from_zip(upload.file.read())
    raise HTTPException(status_code=400, detail="Only .pptx or .zip is supported.")


def normalize_words(words: Iterable[str]) -> list[str]:
    unique_words = sorted({word.strip() for word in words if word.strip()})
    checked_words = check_spelling(unique_words)
    return sorted({word.strip() for word in checked_words if word.strip()})


def insert_words(conn: sqlite3.Connection, words: list[str]) -> int:
    inserted = 0
    now = utc_now()
    for word in words:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO words (word, created_at) VALUES (?, ?)",
            (word, now),
        )
        if cursor.rowcount == 1:
            inserted += 1
    conn.commit()
    return inserted


def select_words_for_user(
    conn: sqlite3.Connection, user_id: int, limit: int
) -> list[str]:
    rows = conn.execute(
        """
        SELECT w.id, w.word, u.last_used_at
        FROM words AS w
        LEFT JOIN user_word_usage AS u
            ON u.word_id = w.id AND u.user_id = ?
        ORDER BY (u.last_used_at IS NOT NULL) ASC,
                 u.last_used_at ASC,
                 w.word ASC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    if not rows:
        return []
    now = utc_now()
    conn.executemany(
        """
        INSERT INTO user_word_usage (user_id, word_id, last_used_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, word_id)
        DO UPDATE SET last_used_at = excluded.last_used_at
        """,
        [(user_id, row["id"], now) for row in rows],
    )
    conn.commit()
    return [str(row["word"]) for row in rows]


def process_upload(
    conn: sqlite3.Connection, upload: UploadFile
) -> dict[str, int | str]:
    extracted = extract_words_from_upload(upload)
    unique_extracted = sorted({word.strip() for word in extracted if word.strip()})
    checked = normalize_words(unique_extracted)
    inserted = insert_words(conn, checked) if checked else 0
    return {
        "filename": upload.filename or "upload",
        "extracted": len(extracted),
        "unique_extracted": len(unique_extracted),
        "checked_unique": len(checked),
        "inserted": inserted,
    }


def render_index(username: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Blitz Croco Words</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; }}
    form {{ margin: 24px 0; padding: 16px; border: 1px solid #ddd; }}
    label {{ display: block; margin-bottom: 8px; }}
    input[type="number"] {{ width: 120px; }}
  </style>
</head>
<body>
  <h1>Blitz Croco Words</h1>
  <p>Signed in as: {username}</p>

  <h2>Upload pptx or zip</h2>
  <form action="/upload" method="post" enctype="multipart/form-data">
    <label>File: <input type="file" name="file" accept=".pptx,.zip" required></label>
    <button type="submit">Upload</button>
  </form>

  <h2>Download words</h2>
  <form action="/words.txt" method="get">
    <label>Word count:
      <input type="number" name="n" min="1" max="{MAX_WORD_COUNT}"
             value="{DEFAULT_WORD_COUNT}" required>
    </label>
    <button type="submit">Download words.txt</button>
  </form>
</body>
</html>
"""


def render_upload_result(username: str, result: dict[str, int | str]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Upload result</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; }}
    a {{ display: inline-block; margin-top: 16px; }}
  </style>
</head>
<body>
  <h1>Upload result</h1>
  <p>Signed in as: {username}</p>
  <ul>
    <li>File: {result["filename"]}</li>
    <li>Extracted: {result["extracted"]}</li>
    <li>Unique extracted: {result["unique_extracted"]}</li>
    <li>Checked unique: {result["checked_unique"]}</li>
    <li>Inserted: {result["inserted"]}</li>
  </ul>
  <a href="/">Back</a>
</body>
</html>
"""


@app.on_event("startup")
def startup() -> None:
    db_path = get_db_path()
    init_db(db_path)
    ensure_default_user(db_path)


@app.get("/", response_class=HTMLResponse)
def index(user: User = Depends(get_current_user)) -> HTMLResponse:
    return HTMLResponse(render_index(user.username))


@app.post("/upload", response_class=HTMLResponse)
def upload_ui(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    result = process_upload(conn, file)
    return HTMLResponse(render_upload_result(user.username, result))


@app.get("/words.txt", response_class=PlainTextResponse)
def words_txt(
    n: int = Query(DEFAULT_WORD_COUNT, ge=1, le=MAX_WORD_COUNT),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> PlainTextResponse:
    words = select_words_for_user(conn, user.id, n)
    payload = "\n".join(words)
    if payload:
        payload += "\n"
    response = PlainTextResponse(payload)
    response.headers["Content-Disposition"] = "attachment; filename=words.txt"
    return response


@app.post("/api/upload")
def api_upload(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, int | str]:
    return process_upload(conn, file)


@app.get("/api/words")
def api_words(
    n: int = Query(DEFAULT_WORD_COUNT, ge=1, le=MAX_WORD_COUNT),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    words = select_words_for_user(conn, user.id, n)
    return {"count": len(words), "words": words}


@app.post("/api/users")
def api_create_user(
    payload: dict[str, str],
    _user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required.")
    if get_user_by_username(conn, username) is not None:
        raise HTTPException(status_code=409, detail="User already exists.")
    create_user(conn, username, password)
    return {"username": username}

