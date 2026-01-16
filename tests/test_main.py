import zipfile
from pathlib import Path

import pytest

import main


def test_read_zipped_file_missing_raises(tmp_path: Path) -> None:
    missing_archive = tmp_path / "missing.zip"

    with pytest.raises(FileNotFoundError):
        main.read_zipped_file(missing_archive, tmp_path / "out.txt")


def test_read_zipped_file_processes_only_pptx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.pptx", b"a")
        archive.writestr("b.PPTX", b"b")
        archive.writestr("notes.txt", b"c")
        archive.writestr("dir/", b"")
        archive.writestr("dir/c.pptx", b"d")

    calls: list[str] = []
    saved: dict[str, object] = {}

    def fake_get_words(file_obj: object) -> list[str]:
        name = getattr(file_obj, "name", "")
        calls.append(name)
        base = Path(name).name.lower()
        mapping = {
            "a.pptx": ["apple", "pear"],
            "b.pptx": ["pear", "banana"],
            "c.pptx": ["cherry"],
        }
        return mapping[base]

    def fake_check_spelling(words: list[str]) -> list[str]:
        saved["checked"] = words
        return ["banana", "cherry"]

    def fake_save_words(words: list[str], output_path: Path) -> None:
        saved["words"] = words
        saved["path"] = output_path

    monkeypatch.setattr(main, "get_words_form_file", fake_get_words)
    monkeypatch.setattr(main, "check_spelling", fake_check_spelling)
    monkeypatch.setattr(main, "save_words_to_file", fake_save_words)

    output_path = tmp_path / "nested" / "words.txt"
    main.read_zipped_file(archive_path, output_path)

    assert len(calls) == 3
    assert saved["checked"] == ["apple", "banana", "cherry", "pear"]
    assert saved["words"] == ["banana", "cherry"]
    assert saved["path"] == output_path
    assert output_path.parent.is_dir()
