from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, localcontext
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Callable
import unicodedata


MAX_MODEL_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 100_000
_EXTENSIONS = {".mod", ".lib", ".cir", ".sp", ".spi", ".spice", ".ckt", ".net", ".txt", ".inc"}
_SUBCKT = re.compile(r"^\s*\.subckt\s+([^\s;$]+)", re.IGNORECASE)
_CAPACITANCE = re.compile(r"(?<![\d.])((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)(?:\s*)([pnum]?)\s*f(?![a-z])")
_SCALES = {"": 0, "m": -3, "u": -6, "n": -9, "p": -12}


@dataclass
class ModelEntry:
    path: Path
    relative_path: str
    subcircuit: str
    comments: str
    _search_text: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        text = _normalize(f"{self.relative_path}\n{self.subcircuit}\n{self.comments}")
        self._search_text = re.sub(r"\s+", "", text + "\n" + _capacitance_tokens(text))


@dataclass
class LibraryIndex:
    root: Path
    entries: list[ModelEntry]
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


class LibraryIndexCancelled(ValueError):
    pass


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise LibraryIndexCancelled("Model library operation was cancelled.")


def read_model_text(path: Path) -> str:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_MODEL_BYTES + 1)
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError("Model exceeds the 8 MiB size limit.")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig")
    else:
        for encoding in ("utf-8", "cp949", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Model text encoding is not supported.")
    if any(ord(char) < 32 and char not in "\t\r\n\f" for char in text):
        raise ValueError("Model contains binary data.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _metadata(text: str) -> tuple[str, str]:
    names: list[str] = []
    comments: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("*"):
            comments.append(line.lstrip()[1:].strip())
            continue
        code, *comment = re.split(r"[;$]", line, maxsplit=1)
        if comment:
            comments.append(comment[0].strip())
        match = _SUBCKT.match(code)
        if match:
            names.append(match.group(1))
    return " ".join(dict.fromkeys(names)), "\n".join(comments)


def build_index(
    root: Path,
    index_path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
) -> LibraryIndex:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The model library root must be a folder.")
    index_path = Path(index_path).absolute()
    result = LibraryIndex(root, [])
    processed = 0
    last_progress = time.monotonic()

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise LibraryIndexCancelled("Model library indexing was cancelled.")

    def walk_error(error: OSError) -> None:
        if Path(error.filename or root) == root:
            raise error
        result.skipped += 1
        result.warnings.append(str(error))

    check_cancelled()
    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        check_cancelled()
        directories[:] = sorted(
            name for name in directories
            if not (Path(directory) / name).is_symlink() and not (Path(directory) / name).is_junction()
        )
        for filename in sorted(filenames):
            check_cancelled()
            path = Path(directory) / filename
            if path.suffix.casefold() not in _EXTENSIONS:
                continue
            processed += 1
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Model links outside the library are not indexed.")
                subcircuit, comments = _metadata(read_model_text(path))
                if subcircuit:
                    if path.resolve() == index_path.resolve():
                        raise FileExistsError("The index destination is a source model file.")
                    result.entries.append(ModelEntry(path, path.relative_to(root).as_posix(), subcircuit, comments))
                else:
                    result.skipped += 1
            except FileExistsError:
                raise
            except (OSError, ValueError) as error:
                result.skipped += 1
                result.warnings.append(f"{path.relative_to(root)}: {error}")
            if len(result.entries) > MAX_ENTRIES:
                raise ValueError("Model library exceeds the 100,000 entry limit.")
            if progress is not None and (processed % 100 == 0 or time.monotonic() - last_progress >= 0.1):
                progress(processed)
                last_progress = time.monotonic()
    result.entries.sort(key=lambda entry: (entry.relative_path.casefold(), entry.relative_path))
    check_cancelled()
    if progress is not None:
        progress(processed)
    descriptor, temporary = tempfile.mkstemp(prefix=".model-index-", suffix=".tmp", dir=index_path.parent)
    os.close(descriptor)
    try:
        connection = sqlite3.connect(temporary)
        try:
            with connection:
                connection.execute("PRAGMA user_version = 1")
                connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("CREATE TABLE entries (relative_path TEXT PRIMARY KEY, subcircuit TEXT NOT NULL, comments TEXT NOT NULL)")
                connection.executemany("INSERT INTO metadata VALUES (?, ?)", [("root", str(root)), ("skipped", str(result.skipped))])
                connection.executemany(
                    "INSERT INTO entries VALUES (?, ?, ?)",
                    ((entry.relative_path, entry.subcircuit, entry.comments) for entry in result.entries),
                )
        finally:
            connection.close()
        check_cancelled()
        if Path(temporary).stat().st_size > MAX_INDEX_BYTES:
            raise ValueError("Model library index exceeds the 256 MiB size limit.")
        os.replace(temporary, index_path)
    except sqlite3.Error as error:
        raise ValueError(f"Could not write model library index: {error}") from error
    finally:
        Path(temporary).unlink(missing_ok=True)
    return result


def load_index(index_path: Path, *, cancelled: Callable[[], bool] | None = None) -> LibraryIndex:
    _check_cancelled(cancelled)
    index_path = Path(index_path).resolve(strict=True)
    if index_path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Model library index exceeds the 256 MiB size limit.")
    try:
        connection = sqlite3.connect(index_path.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ValueError(f"Could not open model library index: {error}") from error
    try:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA query_only = ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_MODEL_BYTES * 3 + 16_384)
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported model library index version.")
        tables = dict(connection.execute("SELECT name, type FROM sqlite_master WHERE name IN ('metadata', 'entries')"))
        if tables != {"metadata": "table", "entries": "table"}:
            raise ValueError("Invalid model library index schema.")
        for table, expected in (("metadata", ["key", "value"]), ("entries", ["relative_path", "subcircuit", "comments"])):
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            if columns != expected:
                raise ValueError("Invalid model library index schema.")
        metadata = dict(connection.execute("SELECT key, value FROM metadata LIMIT 10"))
        root_text = metadata.get("root")
        if not isinstance(root_text, str) or not Path(root_text).is_absolute():
            raise ValueError("Invalid model library root.")
        _check_cancelled(cancelled)
        root = Path(root_text).resolve()
        skipped = int(metadata.get("skipped", "0"))
        if skipped < 0:
            raise ValueError("Invalid skipped file count.")
        entries: list[ModelEntry] = []
        for relative, subcircuit, comments in connection.execute("SELECT relative_path, subcircuit, comments FROM entries LIMIT ?", (MAX_ENTRIES + 1,)):
            _check_cancelled(cancelled)
            if not all(isinstance(value, str) for value in (relative, subcircuit, comments)):
                raise ValueError("Invalid model library entry.")
            relative_path = Path(relative)
            if not relative or "\x00" in relative or relative_path.is_absolute() or relative_path.drive or ".." in relative_path.parts:
                raise ValueError("Model library entry has an unsafe path.")
            path = root / relative_path
            if not path.resolve().is_relative_to(root) or path == root or not subcircuit:
                raise ValueError("Model library entry has an unsafe path or missing subcircuit.")
            entries.append(ModelEntry(path, relative_path.as_posix(), subcircuit, comments))
        if len(entries) > MAX_ENTRIES:
            raise ValueError("Model library exceeds the 100,000 entry limit.")
        entries.sort(key=lambda entry: (entry.relative_path.casefold(), entry.relative_path))
        _check_cancelled(cancelled)
        return LibraryIndex(root, entries, skipped)
    except (sqlite3.Error, TypeError, OverflowError, RuntimeError) as error:
        raise ValueError(f"Invalid model library index: {error}") from error
    finally:
        connection.close()


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("μ", "u")


def _capacitance_tokens(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        # Bound decimal work for numeric fragments in untrusted vendor comments.
        if len(match.group(1)) > 64:
            return match.group(0)
        try:
            with localcontext() as context:
                context.prec = 64
                value = Decimal(match.group(1))
                if abs(value.adjusted()) > 64:
                    return match.group(0)
                value = value.scaleb(_SCALES[match.group(2)])
                return "{cap:" + format(value.normalize(), "f") + "}"
        except DecimalException:
            return match.group(0)

    return _CAPACITANCE.sub(replace, text)


def search_entries(entries: list[ModelEntry], query: str) -> list[ModelEntry]:
    terms = _capacitance_tokens(_normalize(query)).split()
    return [entry for entry in entries if all(term in entry._search_text for term in terms)]


def save_index_as(
    source: Path,
    destination: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> LibraryIndex:
    _check_cancelled(cancelled)
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    index = load_index(source, cancelled=cancelled)
    if source == destination.resolve() or (destination.exists() and source.samefile(destination)):
        return index
    descriptor, temporary = tempfile.mkstemp(prefix=".model-index-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            while chunk := input_file.read(1024 * 1024):
                _check_cancelled(cancelled)
                output.write(chunk)
        _check_cancelled(cancelled)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return index
