import os
from pathlib import Path
from zipfile import ZipFile
from helpers import get_words_form_file, check_spelling, save_words_to_file

ZIP_FILENAME = "croco-blitz-source.zip"


def read_zipped_file():
    current_folder = os.path.dirname(os.path.realpath(__file__))

    words: set[str] = set()

    with ZipFile(Path(current_folder) / 'src' / ZIP_FILENAME) as archive:
        for f in archive.namelist():
            words.update(get_words_form_file(archive.open(f)))

    # 2247 words when lists used
    # 1634 words when set used

    checked_words_string: list[str] = check_spelling(words)
    save_words_to_file(checked_words_string, 'words.txt')


if __name__ == "__main__":
    read_zipped_file()
