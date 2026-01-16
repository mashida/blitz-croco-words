import io
from pathlib import Path

import pytest

import helpers


class DummyShape:
    def __init__(self, text: str, has_text_frame: bool = True) -> None:
        self.text = text
        self.has_text_frame = has_text_frame


class DummySlide:
    def __init__(self, shapes: list[DummyShape]) -> None:
        self.shapes = shapes


class DummyPresentation:
    def __init__(self, _file: io.BytesIO) -> None:
        self.slides = [
            DummySlide(
                [
                    DummyShape("apple\n"),
                    DummyShape("two words"),
                    DummyShape("with-hyphen"),
                    DummyShape("with:colon"),
                    DummyShape("СУПЕРКРОКО"),
                    DummyShape("banana"),
                    DummyShape("skip", has_text_frame=False),
                ]
            )
        ]


def test_get_words_form_file_filters_and_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_file = io.BytesIO(b"data")
    dummy_file.name = "sample.pptx"

    monkeypatch.setattr(helpers, "Presentation", DummyPresentation)

    words = helpers.get_words_form_file(dummy_file)

    assert words == ["apple", "banana"]


def test_check_spelling_empty_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Speller should not be initialized")

    monkeypatch.setattr(helpers, "YandexSpeller", fail)

    assert helpers.check_spelling([]) == []


def test_check_spelling_calls_speller(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummySpeller:
        called_with: str | None = None

        def __init__(self) -> None:
            pass

        def spelled(self, text: str) -> str:
            DummySpeller.called_with = text
            return "one two"

    monkeypatch.setattr(helpers, "YandexSpeller", DummySpeller)

    result = helpers.check_spelling(["one", "two"])

    assert DummySpeller.called_with == "one two"
    assert result == ["one", "two"]


def test_save_words_to_file_writes_lines(tmp_path: Path) -> None:
    output_path = tmp_path / "words.txt"

    helpers.save_words_to_file(["apple", "banana"], output_path)

    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "apple",
        "banana",
    ]
