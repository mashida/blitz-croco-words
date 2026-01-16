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