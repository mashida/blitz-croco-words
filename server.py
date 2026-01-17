from __future__ import annotations

# pylint: disable=too-many-lines

import argparse
import hashlib
import hmac
import logging
import os
import random
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import uvicorn
from dotenv import load_dotenv

from helpers import check_spelling, get_words_form_file

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
DEFAULT_DB_PATH = BASE_DIR / "data" / "words.db"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"
PASSWORD_ITERATIONS = 200_000
DEFAULT_WORD_COUNT = 120
DEFAULT_WORDS_PAGE_SIZE = 200
MAX_WORDS_PAGE_SIZE = 1000
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
SESSION_TTL_SECONDS = 60 * 60 * 24
SESSION_COOKIE_NAME = "croco_session"
MAX_WORD_COUNT = 10_000

security = HTTPBasic()
app = FastAPI()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    is_admin: bool


@dataclass(frozen=True)
class UserSummary:
    id: int
    username: str
    is_admin: bool
    created_at: str


@dataclass(frozen=True)
class WordsPageContext:
    username: str
    words: list[tuple[int, str, str]]
    total: int
    page: int
    per_page: int
    order: str
    message: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_timestamp() -> int:
    return int(time.time())


def format_last_used(value: str | None) -> str:
    if not value:
        return "ещё не использовалось"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "ещё не использовалось"
    return parsed.strftime("%Y.%m.%d %H:%M:%S")


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
                created_at TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        ensure_user_columns(conn)


def ensure_user_columns(conn: sqlite3.Connection) -> None:
    columns: list[str] = [str(row["name"]) for row in conn.execute("PRAGMA table_info(users)")]
    if "is_admin" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()


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
        """
        SELECT id, username, password_salt, password_hash, is_admin, created_at
        FROM users WHERE username = ?
        """,
        (username,),
    ).fetchone()


def create_user(conn: sqlite3.Connection, username: str, password: str, is_admin: bool) -> int:
    salt = secrets.token_bytes(16)
    password_hash = hash_password(password, salt)
    is_admin_value: int = 1 if is_admin else 0
    conn.execute(
        """
        INSERT INTO users (username, password_salt, password_hash, created_at, is_admin)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, salt, password_hash, utc_now(), is_admin_value),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to create user.")
    return int(row["id"])


def ensure_admin_user(db_path: Path) -> None:
    username = os.getenv("APP_USER", DEFAULT_USER)
    password = os.getenv("APP_PASSWORD", DEFAULT_PASSWORD)
    if "APP_USER" not in os.environ or "APP_PASSWORD" not in os.environ:
        logging.warning(
            "APP_USER or APP_PASSWORD not set. Using default credentials."
        )
    with open_connection(db_path) as conn:
        existing = get_user_by_username(conn, username)
        if existing is None:
            create_user(conn, username, password, is_admin=True)
            return
        if int(existing["is_admin"]) != 1:
            conn.execute(
                "UPDATE users SET is_admin = 1 WHERE username = ?",
                (username,),
            )
            conn.commit()


def update_user_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    password_hash = hash_password(password, salt)
    conn.execute(
        """
        UPDATE users
        SET password_salt = ?, password_hash = ?
        WHERE username = ?
        """,
        (salt, password_hash, username),
    )
    conn.commit()


def update_user_admin(conn: sqlite3.Connection, username: str, is_admin: bool) -> None:
    is_admin_value: int = 1 if is_admin else 0
    conn.execute(
        "UPDATE users SET is_admin = ? WHERE username = ?",
        (is_admin_value, username),
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()


def count_admins(conn: sqlite3.Connection) -> int:
    row: sqlite3.Row | None = conn.execute(
        "SELECT COUNT(*) AS count FROM users WHERE is_admin = 1"
    ).fetchone()
    if row is None:
        return 0
    return int(row["count"])


def list_users(conn: sqlite3.Connection) -> list[UserSummary]:
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT id, username, is_admin, created_at
        FROM users
        ORDER BY is_admin DESC, username ASC
        """
    ).fetchall()
    return [
        UserSummary(
            id=int(row["id"]),
            username=str(row["username"]),
            is_admin=bool(int(row["is_admin"])),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def reset_user_usage(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM user_word_usage WHERE user_id = ?", (user_id,))
    conn.commit()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token: str = secrets.token_urlsafe(32)
    now: int = utc_timestamp()
    expires_at: int = now + SESSION_TTL_SECONDS
    conn.execute(
        """
        INSERT INTO sessions (token, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, user_id, now, expires_at),
    )
    conn.commit()
    return token


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def get_user_by_session(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    now: int = utc_timestamp()
    return conn.execute(
        """
        SELECT u.id, u.username, u.password_salt, u.password_hash, u.is_admin, u.created_at
        FROM sessions AS s
        JOIN users AS u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
        """,
        (token, now),
    ).fetchone()


def count_words(conn: sqlite3.Connection) -> int:
    row: sqlite3.Row | None = conn.execute(
        "SELECT COUNT(*) AS count FROM words"
    ).fetchone()
    if row is None:
        return 0
    return int(row["count"])


def list_words(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    limit: int,
    offset: int,
    order: str,
) -> list[tuple[int, str, str]]:
    order_by: str
    if order == "created_desc":
        order_by = "w.created_at DESC, w.word ASC"
    else:
        order_by = "w.word ASC"
    rows: list[sqlite3.Row] = conn.execute(
        f"""
        SELECT w.id, w.word, u.last_used_at
        FROM words AS w
        LEFT JOIN user_word_usage AS u
            ON u.word_id = w.id AND u.user_id = ?
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    ).fetchall()
    return [
        (int(row["id"]), str(row["word"]), format_last_used(row["last_used_at"]))
        for row in rows
    ]


def get_db() -> sqlite3.Connection:
    db_path = get_db_path()
    conn = open_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_current_user_basic(
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
    return User(
        id=int(row["id"]),
        username=str(row["username"]),
        is_admin=bool(int(row["is_admin"])),
    )


def get_current_user_session(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> User:
    token: str | None = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    row = get_user_by_session(conn, token)
    if row is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return User(
        id=int(row["id"]),
        username=str(row["username"]),
        is_admin=bool(int(row["is_admin"])),
    )


def require_admin(user: User = Depends(get_current_user_session)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def require_admin_basic(user: User = Depends(get_current_user_basic)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


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
    unique_words = sorted(
        {clean_word(word) for word in words if clean_word(word)}
    )
    checked_words = check_spelling(unique_words)
    return sorted({clean_word(word) for word in checked_words if clean_word(word)})


def clean_word(word: str) -> str:
    return re.sub(r"[^A-Za-zА-Яа-яЁё]+", "", word).strip()


def update_word(conn: sqlite3.Connection, word_id: int, word: str) -> None:
    conn.execute(
        "UPDATE words SET word = ? WHERE id = ?",
        (word, word_id),
    )
    conn.commit()


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
        SELECT w.id, w.word
        FROM words AS w
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (limit,),
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
    return process_uploads(conn, [upload])


def process_uploads(
    conn: sqlite3.Connection, uploads: list[UploadFile]
) -> dict[str, int | str]:
    extracted: list[str] = []
    filenames: list[str] = []
    for upload in uploads:
        filenames.append(upload.filename or "upload")
        extracted.extend(extract_words_from_upload(upload))
    unique_extracted = sorted({word.strip() for word in extracted if word.strip()})
    checked = normalize_words(unique_extracted)
    inserted = insert_words(conn, checked) if checked else 0
    return {
        "filenames": ", ".join(filenames),
        "extracted": len(extracted),
        "unique_extracted": len(unique_extracted),
        "checked_unique": len(checked),
        "inserted": inserted,
    }


def render_index(username: str, is_admin: bool, total_words: int) -> str:
    admin_link: str = ""
    if is_admin:
        admin_link = '<p><a href="/admin">Открыть админку</a></p>'
    return f"""<!doctype html>
<html lang="ru">
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
  <p>Вы вошли как: {username}</p>
  <p>Всего слов в базе: {total_words}</p>
  <p><a href="/logout">Выйти</a></p>
  <p><a href="/words">Список слов</a></p>
  {admin_link}

  <h2>Загрузка pptx или zip</h2>
  <form action="/upload" method="post" enctype="multipart/form-data">
    <label>Файлы: <input type="file" name="files" accept=".pptx,.zip" multiple required></label>
    <button type="submit">Загрузить</button>
  </form>

  <h2>Скачать слова</h2>
  <form action="/words.txt" method="get">
    <label>Количество слов:
      <input type="number" name="n" min="1" max="{MAX_WORD_COUNT}"
             value="{DEFAULT_WORD_COUNT}" required>
    </label>
    <label>
      <input type="checkbox" name="shuffle"> Перемешать слова
    </label>
    <button type="submit">Скачать words.txt</button>
  </form>

  <h2>Сбросить использование слов</h2>
  <form action="/usage/reset" method="post">
    <button type="submit">Сбросить для меня</button>
  </form>
</body>
</html>
"""


def render_upload_result(username: str, result: dict[str, int | str]) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Результат загрузки</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; }}
    a {{ display: inline-block; margin-top: 16px; }}
  </style>
</head>
<body>
  <h1>Результат загрузки</h1>
  <p>Вы вошли как: {username}</p>
  <ul>
    <li>Файлы: {result["filenames"]}</li>
    <li>Извлечено: {result["extracted"]}</li>
    <li>Уникальных извлечено: {result["unique_extracted"]}</li>
    <li>Проверено уникальных: {result["checked_unique"]}</li>
    <li>Добавлено: {result["inserted"]}</li>
  </ul>
  <a href="/">Назад</a>
</body>
</html>
"""


def render_login_page(username: str, error: str | None) -> str:
    error_block: str = ""
    if error:
        error_block = f'<p style="color:#b00">{error}</p>'
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Вход</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 40px auto; }}
    form {{ margin: 16px 0; padding: 16px; border: 1px solid #ddd; }}
    label {{ display: block; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>Вход</h1>
  {error_block}
  <form action="/login" method="post">
    <label>Логин:
      <input name="username" value="{username}" autocomplete="username" required>
    </label>
    <label>Пароль:
      <input type="password" name="password" autocomplete="current-password" required>
    </label>
    <button type="submit">Войти</button>
  </form>
</body>
</html>
"""


def render_admin_page(
    username: str, users: list[UserSummary], message: str | None
) -> str:
    rows: str = ""
    for user in users:
        admin_label: str = "да" if user.is_admin else "нет"
        rows += (
            "<tr>"
            f"<td>{user.username}</td>"
            f"<td>{admin_label}</td>"
            f"<td>{user.created_at}</td>"
            "</tr>"
        )
    message_block: str = ""
    if message:
        message_block = f'<p style="color:#b00">{message}</p>'
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Админка</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 860px; margin: 40px auto; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    form {{ margin: 16px 0; padding: 12px; border: 1px solid #ddd; }}
    label {{ display: block; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>Админка</h1>
  <p>Вы вошли как: {username}</p>
  <p><a href="/">Назад</a> · <a href="/words">Список слов</a> · <a href="/logout">Выйти</a></p>
  {message_block}

  <h2>Пользователи</h2>
  <table>
    <thead>
      <tr><th>Логин</th><th>Админ</th><th>Создан</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <datalist id="usernames">
    {"".join(f"<option value='{u.username}'></option>" for u in users)}
  </datalist>

  <h2>Создать пользователя</h2>
  <form action="/admin/users/create" method="post">
    <label>Логин: <input name="username" list="usernames" required></label>
    <label>Пароль: <input type="password" name="password" required></label>
    <label><input type="checkbox" name="is_admin"> Админ</label>
    <button type="submit">Создать</button>
  </form>

  <h2>Сменить пароль</h2>
  <form action="/admin/users/password" method="post">
    <label>Логин: <input name="username" list="usernames" required></label>
    <label>Новый пароль: <input type="password" name="password" required></label>
    <button type="submit">Сменить пароль</button>
  </form>

  <h2>Изменить роль</h2>
  <form action="/admin/users/role" method="post">
    <label>Логин: <input name="username" list="usernames" required></label>
    <label><input type="checkbox" name="is_admin"> Админ</label>
    <button type="submit">Изменить роль</button>
  </form>

  <h2>Удалить пользователя</h2>
  <form action="/admin/users/delete" method="post">
    <label>Логин: <input name="username" list="usernames" required></label>
    <button type="submit">Удалить</button>
  </form>

  <h2>Сбросить использование</h2>
  <form action="/admin/users/reset-usage" method="post">
    <label>Логин: <input name="username" list="usernames" required></label>
    <button type="submit">Сбросить</button>
  </form>
</body>
</html>
"""


def build_words_nav(
    *,
    page: int,
    total_pages: int,
    per_page: int,
    order: str,
) -> tuple[str, str]:
    prev_page: int | None = page - 1 if page > 1 else None
    next_page: int | None = page + 1 if page < total_pages else None
    prev_link: str = (
        f'<a href="/words?page={prev_page}&per_page={per_page}&order={order}">Prev</a>'
        if prev_page is not None
        else "<span>Prev</span>"
    )
    next_link: str = (
        f'<a href="/words?page={next_page}&per_page={per_page}&order={order}">Next</a>'
        if next_page is not None
        else "<span>Next</span>"
    )
    return prev_link, next_link


def render_words_page(context: WordsPageContext) -> str:
    rows: str = ""
    for word_id, word, created_at in context.words:
        rows += (
            "<tr>"
            f"<td>{word}</td>"
            f"<td>{created_at}</td>"
            "<td>"
            '<form method="post" action="/words/edit">'
            f'<input type="hidden" name="word_id" value="{word_id}">'
            f'<input name="word" value="{word}" required>'
            '<button type="submit">Сохранить</button>'
            "</form>"
            "</td>"
            "</tr>"
        )
    total_pages: int = max(1, (context.total + context.per_page - 1) // context.per_page)
    prev_link, next_link = build_words_nav(
        page=context.page,
        total_pages=total_pages,
        per_page=context.per_page,
        order=context.order,
    )
    order_alpha_selected: str = "selected" if context.order == "alpha" else ""
    order_created_selected: str = (
        "selected" if context.order == "created_desc" else ""
    )
    message_block: str = ""
    if context.message:
        message_block = f'<p style="color:#b00">{context.message}</p>'
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Слова</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 860px; margin: 40px auto; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    form {{ margin: 12px 0; }}
    nav {{ display: flex; gap: 12px; align-items: center; margin: 12px 0; }}
    nav span {{ color: #666; }}
  </style>
</head>
<body>
  <h1>Слова</h1>
  <p>Вы вошли как: {context.username}</p>
  <p>Всего слов в базе: {context.total}</p>
  <p><a href="/">Назад</a> · <a href="/logout">Выйти</a></p>
  {message_block}

  <form method="get" action="/words">
    <label>Сортировка:
      <select name="order">
        <option value="alpha" {order_alpha_selected}>По алфавиту</option>
        <option value="created_desc" {order_created_selected}>Сначала новые</option>
      </select>
    </label>
    <label>На странице:
      <input type="number" name="per_page" min="1" max="{MAX_WORDS_PAGE_SIZE}"
             value="{context.per_page}" required>
    </label>
    <input type="hidden" name="page" value="1">
    <button type="submit">Применить</button>
  </form>

  <nav>
    {prev_link}
    <span>Страница {context.page} / {total_pages} · Всего {context.total}</span>
    {next_link}
  </nav>

  <table>
    <thead>
      <tr><th>Слово</th><th>Последнее использование</th><th>Правка</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <nav>
    {prev_link}
    <span>Страница {context.page} / {total_pages}</span>
    {next_link}
  </nav>
</body>
</html>
"""


@app.on_event("startup")
def startup() -> None:
    db_path = get_db_path()
    init_db(db_path)
    ensure_admin_user(db_path)


@app.get("/", response_class=HTMLResponse)
def index(user: User = Depends(get_current_user_session)) -> HTMLResponse:
    db_path = get_db_path()
    with open_connection(db_path) as conn:
        total_words: int = count_words(conn)
    return HTMLResponse(render_index(user.username, user.is_admin, total_words))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    token: str | None = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(render_login_page("", None))


@app.post("/login", response_class=HTMLResponse)
def login(
    username: str = Form(...),
    password: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    clean_username: str = username.strip()
    clean_password: str = password.strip()
    row = get_user_by_username(conn, clean_username)
    if row is None or not verify_password(
        clean_password, row["password_salt"], row["password_hash"]
    ):
        return HTMLResponse(
            render_login_page(clean_username, "Неверный логин или пароль."),
            status_code=401,
        )
    token: str = create_session(conn, int(row["id"]))
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/words", response_class=HTMLResponse)
def words_page(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_WORDS_PAGE_SIZE, ge=1, le=MAX_WORDS_PAGE_SIZE),
    order: str = Query("alpha", pattern="^(alpha|created_desc)$"),
    msg: str | None = Query(None),
    user: User = Depends(get_current_user_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    total: int = count_words(conn)
    offset: int = (page - 1) * per_page
    words: list[tuple[int, str, str]] = list_words(
        conn,
        user_id=user.id,
        limit=per_page,
        offset=offset,
        order=order,
    )
    return HTMLResponse(
        render_words_page(
            WordsPageContext(
                username=user.username,
                words=words,
                total=total,
                page=page,
                per_page=per_page,
                order=order,
                message=msg,
            )
        )
    )


@app.get("/logout")
def logout(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    token: str | None = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(conn, token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    users: list[UserSummary] = list_users(conn)
    return HTMLResponse(render_admin_page(user.username, users, None))


@app.post("/admin/users/create")
def admin_create_user(
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str | None = Form(None),
    _user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    clean_username: str = username.strip()
    clean_password: str = password.strip()
    if not clean_username or not clean_password:
        raise HTTPException(status_code=400, detail="Username and password required.")
    if get_user_by_username(conn, clean_username) is not None:
        raise HTTPException(status_code=409, detail="User already exists.")
    create_user(conn, clean_username, clean_password, is_admin=is_admin == "on")
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/password")
def admin_update_password(
    username: str = Form(...),
    password: str = Form(...),
    _user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    clean_username: str = username.strip()
    clean_password: str = password.strip()
    if not clean_username or not clean_password:
        raise HTTPException(status_code=400, detail="Username and password required.")
    if get_user_by_username(conn, clean_username) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    update_user_password(conn, clean_username, clean_password)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/role")
def admin_update_role(
    username: str = Form(...),
    is_admin: str | None = Form(None),
    user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    clean_username: str = username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username required.")
    target: sqlite3.Row | None = get_user_by_username(conn, clean_username)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    target_is_admin: bool = is_admin == "on"
    if not target_is_admin and clean_username == user.username:
        raise HTTPException(status_code=400, detail="Cannot remove admin from self.")
    if not target_is_admin and int(target["is_admin"]) == 1 and count_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove last admin.")
    update_user_admin(conn, clean_username, target_is_admin)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/delete")
def admin_delete_user(
    username: str = Form(...),
    user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    clean_username: str = username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username required.")
    if clean_username == user.username:
        raise HTTPException(status_code=400, detail="Cannot delete self.")
    target: sqlite3.Row | None = get_user_by_username(conn, clean_username)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if int(target["is_admin"]) == 1 and count_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete last admin.")
    delete_user(conn, clean_username)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/upload", response_class=HTMLResponse)
def upload_ui(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Files required.")
    result = process_uploads(conn, files)
    return HTMLResponse(render_upload_result(user.username, result))


@app.get("/words.txt", response_class=PlainTextResponse)
def words_txt(
    n: int = Query(DEFAULT_WORD_COUNT, ge=1, le=MAX_WORD_COUNT),
    shuffle: bool = Query(False),
    user: User = Depends(get_current_user_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> PlainTextResponse:
    words = select_words_for_user(conn, user.id, n)
    if shuffle:
        random.shuffle(words)
    payload = "\n".join(words)
    if payload:
        payload += "\n"
    response = PlainTextResponse(payload)
    response.headers["Content-Disposition"] = "attachment; filename=words.txt"
    return response


@app.post("/words/edit")
def edit_word(
    word_id: int = Form(...),
    word: str = Form(...),
    _user: User = Depends(get_current_user_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    cleaned: str = clean_word(word)
    if not cleaned:
        return RedirectResponse(
            url="/words?msg=Слово+пустое+после+очистки",
            status_code=303,
        )
    checked_words: list[str] = check_spelling([cleaned])
    checked: str = clean_word(checked_words[0]) if checked_words else ""
    if not checked:
        return RedirectResponse(
            url="/words?msg=Проверка+не+дала+валидного+слова",
            status_code=303,
        )
    try:
        update_word(conn, int(word_id), checked)
    except sqlite3.IntegrityError:
        return RedirectResponse(
            url="/words?msg=Такое+слово+уже+есть+в+базе",
            status_code=303,
        )
    return RedirectResponse(url="/words?msg=Слово+обновлено", status_code=303)


@app.post("/api/upload")
def api_upload(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user_basic),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, int | str]:
    return process_upload(conn, file)


@app.get("/api/words")
def api_words(
    n: int = Query(DEFAULT_WORD_COUNT, ge=1, le=MAX_WORD_COUNT),
    user: User = Depends(get_current_user_basic),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    words = select_words_for_user(conn, user.id, n)
    return {"count": len(words), "words": words}


@app.post("/api/users")
def api_create_user(
    payload: dict[str, str],
    _user: User = Depends(require_admin_basic),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    username = payload.get("username", "").strip()
    password = payload.get("password", "").strip()
    is_admin_raw: str = payload.get("is_admin", "false").strip().lower()
    is_admin: bool = is_admin_raw in {"1", "true", "yes", "on"}
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required.")
    if get_user_by_username(conn, username) is not None:
        raise HTTPException(status_code=409, detail="User already exists.")
    create_user(conn, username, password, is_admin=is_admin)
    return {"username": username}


@app.post("/usage/reset")
def reset_usage(
    user: User = Depends(get_current_user_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    reset_user_usage(conn, user.id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/admin/users/reset-usage")
def admin_reset_usage(
    username: str = Form(...),
    _user: User = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    clean_username: str = username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username required.")
    target: sqlite3.Row | None = get_user_by_username(conn, clean_username)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    reset_user_usage(conn, int(target["id"]))
    return RedirectResponse(url="/admin", status_code=303)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Blitz Croco Words server.")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Bind host (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("APP_PORT", str(DEFAULT_PORT))),
        help="Bind port (default: APP_PORT or 8000).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable auto-reload for development.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        "server:app",
        host=str(args.host),
        port=int(args.port),
        reload=bool(args.dev),
    )


if __name__ == "__main__":
    main()
