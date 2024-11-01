"""
Задача 2024.10.23.06

Разархивируйте архив и достаньте один файл презентации.

Откройте файл презентации в программе с помощью объекта Presentation библиотеки pptx
"""
import os
from typing import IO, TextIO

from pyaspeller import YandexSpeller

FILE_NAME = "Osennyaya_igra_3.pptx"

from pathlib import Path

from pptx import Presentation


def is_not_valid(text: str) -> bool:
    return ' ' in text or '-' in text or ':' in text or 'СУПЕРКРОКО' in text


def get_words_form_file(file: IO[bytes] | TextIO) -> list[str]:
    print(f"getting words from {file.name} file")

    prs = Presentation(file)

    result: list[str] = []

    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if is_not_valid(shape.text):
                continue
            result.append(shape.text.strip())

    print(f"getting words done. got {len(result)} words")

    return result


def check_spelling(words: set[str]) -> list[str]:
    print(f"checking {len(words)} words for spelling with Yandex Speller")

    speller = YandexSpeller()
    result = speller.spelled(' '.join(words))

    print(f"spell checking done")
    return result.split(' ')


def save_words_to_file(words: list[str], filename: str) -> None:
    print(f"saving {len(words)} words to {filename} file")

    with open(file=filename, mode='w', encoding='utf-8') as file:
        file.writelines([word + "\n" for word in words])

    print(f"saving words done")


if __name__ == '__main__':
    with open('Osennyaya_igra_3.pptx', 'r') as f:
        print(get_words_form_file(f))
