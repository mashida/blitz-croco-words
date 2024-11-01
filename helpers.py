"""
Задача 2024.10.23.06

Разархивируйте архив и достаньте один файл презентации.

Откройте файл презентации в программе с помощью объекта Presentation библиотеки pptx
"""
import os
from typing import IO, TextIO

FILE_NAME = "Osennyaya_igra_3.pptx"

from pathlib import Path

from pptx import Presentation


def get_words_form_file(file: IO[bytes] | TextIO) -> list[str]:
    prs = Presentation(file)

    result: list[str] = []

    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if ' ' in shape.text or '-' in shape.text or ':' in shape.text or 'СУПЕРКРОКО' in shape.text:
                continue
            result.append(shape.text.strip())

    return result


if __name__ == '__main__':
    with open('Osennyaya_igra_3.pptx', 'r') as f:
        print(get_words_form_file(f))