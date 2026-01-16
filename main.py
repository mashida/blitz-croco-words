import argparse
from pathlib import Path
from zipfile import ZipFile
from helpers import get_words_form_file, check_spelling, save_words_to_file

ZIP_FILENAME = "croco-blitz-source.zip"
DEFAULT_ARCHIVE_PATH = Path(__file__).resolve().parent / "src" / ZIP_FILENAME
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "words.txt"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract words from pptx files inside a zip archive."
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=DEFAULT_ARCHIVE_PATH,
        help="Path to the zip archive with pptx files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to the output words.txt file.",
    )
    return parser.parse_args()


def read_zipped_file(archive_path: Path, output_path: Path) -> None:
    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    words: set[str] = set()

    with ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if Path(info.filename).suffix.lower() != ".pptx":
                continue
            with archive.open(info) as file:
                words.update(get_words_form_file(file))

    sorted_words = sorted(words)
    checked_words = check_spelling(sorted_words)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_words_to_file(checked_words, output_path)


if __name__ == "__main__":
    args = parse_args()
    read_zipped_file(args.archive, args.output)
