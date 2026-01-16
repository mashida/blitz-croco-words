# Cloud Agent Guide

## Setup
- Install uv if needed: `python3 -m pip install --user uv`
- Sync dependencies: `uv sync --extra dev`

## Tests
- Run unit tests: `uv run pytest`

## Linting
- Ruff: `uv run ruff check .`
- Pylint: `uv run pylint main.py helpers.py check_spell.py current.py proper-current.py`
- Note: `tests/` are excluded from linting in `pyproject.toml`.

## Manual testing
- Default input archive: `src/croco-blitz-source.zip`
- Example run: `uv run python main.py --archive src/croco-blitz-source.zip --output words.txt`
- Verify `words.txt` is created and contains expected words.
