import os
from pathlib import Path
from zipfile import ZipFile

ZIP_FILENAME = "croco-blitz-source.zip"


def read_zipped_file():
    current_folder = os.path.dirname(os.path.realpath(__file__))

    with ZipFile(Path(current_folder) / 'src' / ZIP_FILENAME) as archive:
        for f in archive.namelist():
            print(f)


if __name__ == "__main__":
    read_zipped_file()
