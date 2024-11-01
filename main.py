import os
from pathlib import Path
from zipfile import ZipFile
from helpers import get_words_form_file

ZIP_FILENAME = "croco-blitz-source.zip"


def read_zipped_file():
    current_folder = os.path.dirname(os.path.realpath(__file__))

    words = []

    with ZipFile(Path(current_folder) / 'src' / ZIP_FILENAME) as archive:
        for f in archive.namelist():
            words.extend(get_words_form_file(archive.open(f)))

    for index, word in enumerate(words):
        print(f"[{index}] {word}")


if __name__ == "__main__":
    read_zipped_file()
