from __future__ import annotations

from dataclasses import dataclass, field
import mmap
from pathlib import Path
import re
import shutil
from typing import Callable, Mapping, Sequence


_PARTIAL_RE = re.compile(r"^\.PartialCkt\s+(.+?)\s+ExtNode\s*=\s*(.*)$", re.IGNORECASE)
_CONNECT_RE = re.compile(r"^\.Connect\s+(\S+)\s+(\S+)(?:\s+(.*))?$", re.IGNORECASE)
_USAGE_RE = re.compile(r"\bUsage\s*=\s*(\S+)", re.IGNORECASE)
ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class PartialCktBlock:
    component_name: str
    ext_nodes: list[str]
    start_line: int
    end_line: int
    block_start_offset: int
    body_start_offset: int
    body_end_offset: int
    block_end_offset: int
    header_lines: list[str]

    @property
    def port_count(self) -> int:
        return len(self.ext_nodes)


@dataclass(frozen=True)
class RefDesRecord:
    component_name: str
    refdes_name: str
    activation_status: str
    connect_line_start_offset: int | None = None
    connect_line_end_offset: int | None = None
    connect_line: str | None = None


@dataclass(frozen=True)
class SpdInventory:
    blocks: list[PartialCktBlock]
    refdes_records: list[RefDesRecord]
    refdes_by_component: dict[str, list[RefDesRecord]] = field(init=False)

    def __post_init__(self) -> None:
        grouped: dict[str, list[RefDesRecord]] = {}
        for record in self.refdes_records:
            grouped.setdefault(record.component_name, []).append(record)
        object.__setattr__(self, "refdes_by_component", grouped)


def scan_spd(path: str | Path, progress_callback: ProgressCallback | None = None) -> list[PartialCktBlock]:
    return scan_spd_inventory(path, progress_callback).blocks


def scan_spd_inventory(path: str | Path, progress_callback: ProgressCallback | None = None) -> SpdInventory:
    """Scan an SPD file for PartialCkt blocks without decoding block bodies."""
    spd_path = Path(path)
    blocks: list[PartialCktBlock] = []
    refdes_records: list[RefDesRecord] = []
    file_size = spd_path.stat().st_size
    _report_progress(progress_callback, "Opening SPD file", 0, file_size)
    if file_size == 0:
        _report_progress(progress_callback, "SPD file is empty", 0, 0)
        return SpdInventory(blocks=[], refdes_records=[])

    with spd_path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        _report_progress(progress_callback, "Scanning for PartialCkt blocks", 0, file_size)
        cursor = 0
        counted_offset = 0
        counted_newlines = 0

        def line_number_at(offset: int) -> int:
            nonlocal counted_offset, counted_newlines
            counted_newlines += _count_newlines(data, counted_offset, offset)
            counted_offset = offset
            return counted_newlines + 1

        while True:
            block_start_offset = _find_line_marker(
                data,
                b".PartialCkt",
                cursor,
                progress_callback,
                "Searching .PartialCkt markers",
            )
            if block_start_offset < 0:
                break

            start_line = line_number_at(block_start_offset)
            header_end_offset = _line_end_offset(data, block_start_offset)
            header_lines = [_strip_newline(_decode_line(data[block_start_offset:header_end_offset]))]
            body_start_offset = header_end_offset

            while body_start_offset < len(data):
                next_header_end = _line_end_offset(data, body_start_offset)
                raw_line = data[body_start_offset:next_header_end]
                if not raw_line.lstrip(b" \t").startswith(b"+"):
                    break
                header_lines.append(_strip_newline(_decode_line(raw_line)))
                body_start_offset = next_header_end

            component_name = _parse_component_name(header_lines[0])
            _report_progress(
                progress_callback,
                f"Found {component_name} at line {start_line}",
                block_start_offset,
                file_size,
            )
            end_offset = _find_line_marker(
                data,
                b".EndPartialCkt",
                body_start_offset,
                progress_callback,
                f"Searching end of {component_name}",
            )
            if end_offset < 0:
                body_end_offset = len(data)
                end_line = line_number_at(len(data))
                block_end_offset = len(data)
            else:
                body_end_offset = end_offset
                end_line = line_number_at(end_offset)
                block_end_offset = _line_end_offset(data, end_offset)

            blocks.append(
                PartialCktBlock(
                    component_name=component_name,
                    ext_nodes=_parse_ext_nodes(header_lines),
                    start_line=start_line,
                    end_line=end_line,
                    block_start_offset=block_start_offset,
                    body_start_offset=body_start_offset,
                    body_end_offset=body_end_offset,
                    block_end_offset=block_end_offset,
                    header_lines=header_lines,
                )
            )
            cursor = block_end_offset

        _report_progress(progress_callback, "Scanning for RefDes connections", 0, file_size)
        connect_cursor = 0
        while True:
            connect_offset = _find_line_marker(
                data,
                b".Connect",
                connect_cursor,
                progress_callback,
                "Searching .Connect markers",
            )
            if connect_offset < 0:
                break
            connect_end_offset = _line_end_offset(data, connect_offset)
            connect_line = _decode_line(data[connect_offset:connect_end_offset])
            record = _parse_connect_line(
                connect_line,
                start_offset=connect_offset,
                end_offset=connect_end_offset,
            )
            if record is not None:
                refdes_records.append(record)
            connect_cursor = connect_end_offset

    _report_progress(progress_callback, f"Scan complete: {len(blocks)} PartialCkt blocks", file_size, file_size)
    return SpdInventory(blocks=blocks, refdes_records=refdes_records)


def read_block_body(path: str | Path, block: PartialCktBlock) -> str:
    with Path(path).open("rb") as handle:
        handle.seek(block.body_start_offset)
        raw = handle.read(block.body_end_offset - block.body_start_offset)
    return _normalize_newlines(raw.decode("utf-8"))


def write_spd_with_replacements(
    source_path: str | Path,
    output_path: str | Path,
    blocks: Sequence[PartialCktBlock],
    replacements: Mapping[str, str],
    *,
    refdes_component_changes: Mapping[str, str] | None = None,
    refdes_records: Sequence[RefDesRecord] | None = None,
) -> None:
    """Write a new SPD, replacing selected bodies and RefDes component links."""
    source = Path(source_path)
    output = Path(output_path)
    replacement_by_offset: dict[int, tuple[str, int]] = {
        block.body_start_offset: (_normalize_model_text(replacements[block.component_name]), block.body_end_offset)
        for block in blocks
        if block.component_name in replacements
    }

    if refdes_component_changes:
        records = refdes_records if refdes_records is not None else scan_spd_inventory(source).refdes_records
        for record in records:
            new_component = refdes_component_changes.get(record.refdes_name)
            if new_component is None:
                continue
            if record.connect_line_start_offset is None or record.connect_line_end_offset is None or record.connect_line is None:
                raise ValueError(f"RefDes {record.refdes_name} does not include .Connect line metadata.")
            replacement_by_offset[record.connect_line_start_offset] = (
                _replace_connect_component(record.connect_line, new_component),
                record.connect_line_end_offset,
            )

    with source.open("rb", buffering=1024 * 1024) as src, output.open("wb", buffering=1024 * 1024) as dst:
        cursor = 0
        for start_offset in sorted(replacement_by_offset):
            replacement, end_offset = replacement_by_offset[start_offset]
            _copy_range(src, dst, cursor, start_offset)
            dst.write(replacement.encode("utf-8"))
            src.seek(end_offset)
            cursor = end_offset
        src.seek(cursor)
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def _copy_range(src, dst, start: int, end: int) -> None:
    src.seek(start)
    remaining = end - start
    while remaining > 0:
        chunk = src.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        dst.write(chunk)
        remaining -= len(chunk)


def _parse_component_name(first_header_line: str) -> str:
    match = _PARTIAL_RE.match(first_header_line)
    if not match:
        return first_header_line.split(maxsplit=1)[1].strip() if " " in first_header_line else first_header_line
    return match.group(1).strip()


def _parse_ext_nodes(header_lines: Sequence[str]) -> list[str]:
    match = _PARTIAL_RE.match(header_lines[0])
    if not match:
        return []
    ext_text = [match.group(2)]
    for continuation in header_lines[1:]:
        stripped = continuation.lstrip()
        ext_text.append(stripped[1:] if stripped.startswith("+") else stripped)
    return [token for token in " ".join(ext_text).split() if token]


def _parse_connect_line(
    line: str,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> RefDesRecord | None:
    match = _CONNECT_RE.match(_strip_newline(line))
    if not match:
        return None
    return RefDesRecord(
        component_name=match.group(2),
        refdes_name=match.group(1),
        activation_status=_activation_status(match.group(3) or ""),
        connect_line_start_offset=start_offset,
        connect_line_end_offset=end_offset,
        connect_line=line,
    )


def _replace_connect_component(line: str, component_name: str) -> str:
    match = re.match(r"^(\.Connect\s+\S+\s+)(\S+)(.*)$", line, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(f"Invalid .Connect line: {_strip_newline(line)}")
    return f"{match.group(1)}{component_name}{match.group(3)}"


def _activation_status(attributes: str) -> str:
    match = _USAGE_RE.search(attributes)
    if not match:
        return "Automatic"
    usage = match.group(1)
    if usage == "0b1000":
        return "Enabled"
    if usage == "0b111000":
        return "Disabled"
    return "Unknown"


def _decode_line(raw_line: bytes) -> str:
    return raw_line.decode("utf-8")


def _find_line_marker(
    data: mmap.mmap,
    marker: bytes,
    start: int,
    progress_callback: ProgressCallback | None = None,
    progress_message: str = "Scanning SPD file",
) -> int:
    cursor = start
    chunk_size = 16 * 1024 * 1024
    while cursor < len(data):
        chunk_end = min(cursor + chunk_size, len(data))
        offset = data.find(marker, cursor, chunk_end)
        if offset < 0:
            _report_progress(progress_callback, progress_message, chunk_end, len(data))
            if chunk_end == len(data):
                return -1
            cursor = max(chunk_end - len(marker), cursor + 1)
            continue
        if offset == 0 or data[offset - 1] == 0x0A:
            return offset
        cursor = offset + 1
    return -1


def _line_end_offset(data: mmap.mmap, start: int) -> int:
    newline = data.find(b"\n", start)
    return len(data) if newline < 0 else newline + 1


def _count_newlines(data: mmap.mmap, start: int, end: int) -> int:
    count = 0
    cursor = start
    chunk_size = 8 * 1024 * 1024
    while cursor < end:
        chunk_end = min(cursor + chunk_size, end)
        count += data[cursor:chunk_end].count(b"\n")
        cursor = chunk_end
    return count


def _report_progress(callback: ProgressCallback | None, message: str, current: int, total: int) -> None:
    if callback is not None:
        callback(message, current, total)


def _strip_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_model_text(text: str) -> str:
    normalized = _normalize_newlines(text)
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
