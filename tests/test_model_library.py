from pathlib import Path
import os
import sqlite3
import subprocess

import pytest

from spd_model_injector.core import model_library as library


MODEL = ".SUBCKT CAP P N\nC1 P N 100n\n.ENDS CAP\n"


def model(root: Path, relative: str, text: str = MODEL) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_recursive_filename_folder_subcircuit_and_comments_search(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model(root, "Murata/0603/GRM.MOD", "* X7R 0.1 uF\n" + MODEL.replace("CAP P N", "DECOUPLER P N ; 16 V") + "$ tolerance 10%\n")
    model(root, "Other/0402/another.lib")
    model(root, "datasheet.txt", "* .SUBCKT COMMENTED P N\nThis is documentation")
    model(root, "unrelated.pdf", MODEL)
    progress: list[int] = []

    index = library.build_index(root, tmp_path / "elsewhere.sqlite", progress=progress.append)

    assert len(index.entries) == 2
    assert index.skipped == 1
    assert progress[-1] == 3
    for query in ("murata 0603 grm", "deCOUPLER X7r", "16v 10%", "0603 100nF", "Murata 100000 pF"):
        assert [entry.relative_path for entry in library.search_entries(index.entries, query)] == ["Murata/0603/GRM.MOD"]
    assert library.search_entries(index.entries, "Murata 0402") == []
    assert library.search_entries(index.entries, " ") == index.entries
    assert library.load_index(tmp_path / "elsewhere.sqlite") == library.LibraryIndex(index.root, index.entries, index.skipped)


@pytest.mark.parametrize("query", ["0.1uF", "0.1 uF", "100nF", "100 n F", "100000pF", "0.1µF", "0.1μF", "１００ｎＦ", "1e-7F"])
def test_capacitance_equivalent_units_and_unicode(tmp_path: Path, query: str) -> None:
    path = model(tmp_path, "part.mod")
    entries = [library.ModelEntry(path, "part.mod", "CAP", "X7R 0.1 μF 0603")]
    assert library.search_entries(entries, query) == entries


@pytest.mark.parametrize("query", ["1uF", "10nF", "1pF", "100mF", "100nfarads", "0.1uF 0402"])
def test_capacitance_search_does_not_match_number_or_prefix_collisions(tmp_path: Path, query: str) -> None:
    entries = [library.ModelEntry(tmp_path / "p.mod", "p.mod", "CAP", "0.1uF 0603")]
    assert library.search_entries(entries, query) == []


def test_search_bounds_decimal_work_and_reuses_entry_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    huge_number = "9" * 2000 + "uF"
    entry = library.ModelEntry(tmp_path / "p.mod", "p.mod", "CAP", huge_number + " 100nF")
    original = library._capacitance_tokens
    normalized_calls: list[str] = []

    def tokens(text: str) -> str:
        normalized_calls.append(text)
        return original(text)

    monkeypatch.setattr(library, "_capacitance_tokens", tokens)
    assert library.search_entries([entry], "0.1uF") == [entry]
    assert library.search_entries([entry], huge_number) == [entry]
    assert normalized_calls == ["0.1uf", huge_number.casefold()]


def test_search_does_not_expand_untrusted_exponents(tmp_path: Path) -> None:
    huge_exponent = "1e99999999999999999999999F"
    entry = library.ModelEntry(tmp_path / "p.mod", "p.mod", "CAP", huge_exponent + " 1e-7F")
    assert library.search_entries([entry], huge_exponent) == [entry]
    assert library.search_entries([entry], "100nF") == [entry]
    assert library.search_entries([entry], "7F") == []


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp949", "cp1252"])
def test_supported_encodings(tmp_path: Path, encoding: str) -> None:
    description = "용량 100nF" if encoding == "cp949" else "100nF ±10%"
    text = "* " + description + "\r\n" + MODEL
    path = tmp_path / "part.mod"
    path.write_bytes(text.encode(encoding))
    assert library.read_model_text(path) == text.replace("\r\n", "\n")
    assert library.build_index(tmp_path, tmp_path / "index.db").entries[0].comments == description


def test_update_rebuild_reflects_add_delete_and_change(tmp_path: Path) -> None:
    root = tmp_path / "models"
    removed = model(root, "removed.mod")
    changed = model(root, "changed.mod", "* 100nF\n" + MODEL)
    index_path = tmp_path / "index.db"
    library.build_index(root, index_path)
    removed.unlink()
    changed.write_text("* 22uF\n" + MODEL.replace("CAP", "NEW"), encoding="utf-8")
    model(root, "added.mod")

    result = library.build_index(root, index_path)

    assert [entry.relative_path for entry in result.entries] == ["added.mod", "changed.mod"]
    assert [entry.subcircuit for entry in library.search_entries(result.entries, "22uF")] == ["NEW"]
    assert library.load_index(index_path) == result


def test_cancel_and_failed_root_preserve_previous_index(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model(root, "a.mod")
    destination = tmp_path / "index.db"
    original = library.build_index(root, destination)
    original_bytes = destination.read_bytes()
    model(root, "b.mod")
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(library.LibraryIndexCancelled):
        library.build_index(root, destination, cancelled=cancelled)
    with pytest.raises(OSError):
        library.build_index(tmp_path / "missing", destination)
    assert destination.read_bytes() == original_bytes
    assert library.load_index(destination) == original
    assert not list(tmp_path.glob(".model-index-*"))


def test_cancellation_after_database_write_preserves_index(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model(root, "a.mod")
    destination = tmp_path / "index.db"
    library.build_index(root, destination)
    original = destination.read_bytes()
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls == 5

    with pytest.raises(library.LibraryIndexCancelled):
        library.build_index(root, destination, cancelled=cancelled)
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".model-index-*"))


def test_save_as_preserves_root_and_same_file_is_safe(tmp_path: Path) -> None:
    model(tmp_path / "models", "part.mod")
    source = tmp_path / "original.db"
    index = library.build_index(tmp_path / "models", source)
    original = source.read_bytes()
    assert library.save_index_as(source, source) == index
    assert source.read_bytes() == original
    destination = tmp_path / "copy.db"
    assert library.save_index_as(source, destination) == index
    assert library.load_index(destination) == index
    assert destination.read_bytes() == original


@pytest.mark.parametrize("cancel_at", [1, 2, 4, 5])
def test_load_can_cancel_before_root_during_entries_and_after_processing(tmp_path: Path, cancel_at: int) -> None:
    root = tmp_path / "models"
    model(root, "a.mod")
    model(root, "b.mod")
    index_path = tmp_path / "index.db"
    expected = library.build_index(root, index_path)
    original = index_path.read_bytes()
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls == cancel_at

    with pytest.raises(library.LibraryIndexCancelled):
        library.load_index(index_path, cancelled=cancelled)
    assert calls == cancel_at
    assert index_path.read_bytes() == original
    assert library.load_index(index_path) == expected


def test_cancelled_load_does_not_resolve_a_missing_index(tmp_path: Path) -> None:
    with pytest.raises(library.LibraryIndexCancelled):
        library.load_index(tmp_path / "missing.db", cancelled=lambda: True)


@pytest.mark.parametrize("cancel_at", [1, 4, 6, 7])
def test_save_as_cancellation_preserves_existing_destination(tmp_path: Path, cancel_at: int) -> None:
    root = tmp_path / "models"
    model(root, "part.mod")
    source = tmp_path / "source.db"
    library.build_index(root, source)
    original_source = source.read_bytes()
    destination = tmp_path / "destination.db"
    destination.write_bytes(b"Keep the previous destination")
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls == cancel_at

    with pytest.raises(library.LibraryIndexCancelled):
        library.save_index_as(source, destination, cancelled=cancelled)
    assert calls == cancel_at
    assert source.read_bytes() == original_source
    assert destination.read_bytes() == b"Keep the previous destination"
    assert not list(tmp_path.glob(".model-index-*"))


def test_bad_indexes_and_unsafe_paths_are_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "invalid.db"
    destination.write_bytes(b"this is not SQLite")
    with pytest.raises(ValueError):
        library.load_index(destination)
    model(tmp_path / "models", "part.mod")
    library.build_index(tmp_path / "models", destination)
    with sqlite3.connect(destination) as connection:
        connection.execute("UPDATE entries SET relative_path = '../outside.mod'")
    with pytest.raises(ValueError, match="unsafe path"):
        library.load_index(destination)


def test_external_database_views_are_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "view.db"
    with sqlite3.connect(destination) as connection:
        connection.execute("PRAGMA user_version = 1")
        connection.execute("CREATE VIEW metadata AS SELECT 'root' AS key, '/' AS value")
        connection.execute("CREATE TABLE entries (relative_path, subcircuit, comments)")
    with pytest.raises(ValueError, match="schema"):
        library.load_index(destination)


def test_large_binary_unreadable_and_non_model_files_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    good = model(root, "good.mod")
    model(root, "no-subckt.txt", "Capacitor datasheet")
    binary = model(root, "binary.mod")
    binary.write_bytes(b"\x00" + MODEL.encode())
    big = model(root, "large.mod")
    big.write_bytes(b" " * (library.MAX_MODEL_BYTES + 1))
    unreadable = model(root, "unreadable.mod")
    original = library.read_model_text

    def read(path: Path) -> str:
        if path == unreadable:
            raise PermissionError("Test denied access")
        return original(path)

    monkeypatch.setattr(library, "read_model_text", read)
    index = library.build_index(root, tmp_path / "index.db")
    assert [entry.path for entry in index.entries] == [good]
    assert index.skipped == 4
    assert len(index.warnings) == 3
    with pytest.raises(ValueError, match="8 MiB"):
        original(big)
    with pytest.raises(ValueError, match="binary"):
        original(binary)


def test_root_scan_failure_and_replace_failure_preserve_old_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    model(root, "part.mod")
    destination = tmp_path / "index.db"
    library.build_index(root, destination)
    original = destination.read_bytes()
    real_walk = os.walk

    def broken_walk(top: Path, **kwargs: object):
        kwargs["onerror"](PermissionError(13, "Access denied", str(top)))
        return iter(())

    monkeypatch.setattr(library.os, "walk", broken_walk)
    with pytest.raises(PermissionError):
        library.build_index(root, destination)
    monkeypatch.setattr(library.os, "walk", real_walk)

    def broken_replace(*args: object) -> None:
        raise PermissionError("Destination locked")

    monkeypatch.setattr(library.os, "replace", broken_replace)
    with pytest.raises(PermissionError):
        library.build_index(root, destination)
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".model-index-*"))


def test_build_cannot_overwrite_a_source_model(tmp_path: Path) -> None:
    source = model(tmp_path, "part.mod")
    with pytest.raises(FileExistsError, match="source model"):
        library.build_index(tmp_path, source)
    assert source.read_text(encoding="utf-8") == MODEL


def test_directory_links_are_not_traversed(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model(root, "good.mod")
    external = tmp_path / "external"
    model(external, "outside.mod")
    link = root / "linked"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable on this system")
    assert len(library.build_index(root, tmp_path / "index.db").entries) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions only")
def test_windows_junctions_are_not_traversed(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model(root, "good.mod")
    external = tmp_path / "external"
    model(external, "outside.mod")
    junction = root / "linked"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(external)], capture_output=True)
    assert created.returncode == 0
    assert junction.is_junction()
    assert [entry.relative_path for entry in library.build_index(root, tmp_path / "index.db").entries] == ["good.mod"]


def test_sqlite_write_failure_and_entry_limit_preserve_old_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model(tmp_path / "models", "a.mod")
    destination = tmp_path / "index.db"
    library.build_index(tmp_path / "models", destination)
    original = destination.read_bytes()
    real_connect = library.sqlite3.connect

    def broken_connect(*args: object, **kwargs: object):
        raise sqlite3.OperationalError("Test write error")

    monkeypatch.setattr(library.sqlite3, "connect", broken_connect)
    with pytest.raises(ValueError, match="write"):
        library.build_index(tmp_path / "models", destination)
    monkeypatch.setattr(library.sqlite3, "connect", real_connect)
    monkeypatch.setattr(library, "MAX_ENTRIES", 0)
    with pytest.raises(ValueError, match="entry limit"):
        library.build_index(tmp_path / "models", destination)
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".model-index-*"))
