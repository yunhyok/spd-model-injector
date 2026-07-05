from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Callable, Mapping, Sequence


_PARTIAL_RE = re.compile(r"^\.PartialCkt\s+(.+?)\s+ExtNode\s*=\s*(.*)$", re.IGNORECASE)
_PARTIAL_START_RE = re.compile(r"^\.PartialCkt(?:\s|$)", re.IGNORECASE)
_END_PARTIAL_RE = re.compile(r"^\.EndPartialCkt(?:\s|$)", re.IGNORECASE)
_CONNECT_RE = re.compile(r"^\.Connect\s+(\S+)\s+(\S+)(?:\s+(.*))?$", re.IGNORECASE)
_USAGE_RE = re.compile(r"\bUsage\s*=\s*(\S+)", re.IGNORECASE)

_CHUNK_SIZE = 1024 * 1024
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
    """Scan an SPD file for PartialCkt blocks and RefDes links without loading bodies into memory."""
    spd_path = Path(path)
    blocks: list[PartialCktBlock] = []
    refdes_records: list[RefDesRecord] = []
    line_no = 0
    file_size = spd_path.stat().st_size
    _report_progress(progress_callback, "Opening SPD file", 0, file_size)

    with spd_path.open("rb", buffering=_CHUNK_SIZE) as handle:
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            line_no += 1
            line_text = _decode_line(raw_line)

            if _PARTIAL_START_RE.match(line_text):
                block_start_offset = line_start
                start_line = line_no
                header_lines = [_strip_newline(line_text)]
                body_start_offset = handle.tell()
                pending: tuple[int, bytes, str, int] | None = None

                while True:
                    next_start = handle.tell()
                    next_raw = handle.readline()
                    if not next_raw:
                        break
                    line_no += 1
                    next_text = _decode_line(next_raw)
                    if next_text.lstrip().startswith("+"):
                        header_lines.append(_strip_newline(next_text))
                        body_start_offset = handle.tell()
                        continue
                    pending = (next_start, next_raw, next_text, line_no)
                    break

                body_end_offset = body_start_offset
                end_line = line_no
                block_end_offset = body_start_offset

                if pending is not None:
                    current_start, current_raw, current_text, current_line = pending
                    while True:
                        if _END_PARTIAL_RE.match(current_text):
                            body_end_offset = current_start
                            end_line = current_line
                            block_end_offset = current_start + len(current_raw)
                            break
                        current_start = handle.tell()
                        current_raw = handle.readline()
                        if not current_raw:
                            body_end_offset = handle.tell()
                            block_end_offset = handle.tell()
                            break
                        line_no += 1
                        current_line = line_no
                        current_text = _decode_line(current_raw)

                component_name = _parse_component_name(header_lines[0])
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
                _report_progress(
                    progress_callback,
                    f"Found {component_name} at line {start_line}",
                    block_end_offset,
                    file_size,
                )
                continue

            record = _parse_connect_line(line_text, start_offset=line_start, end_offset=line_start + len(raw_line))
            if record is not None:
                refdes_records.append(record)

    _report_progress(
        progress_callback,
        f"Scan complete: {len(blocks)} PartialCkt blocks, {len(refdes_records)} RefDes records",
        file_size,
        file_size,
    )
    return SpdInventory(blocks=blocks, refdes_records=refdes_records)


def read_block_body(path: str | Path, block: PartialCktBlock) -> str:
    with Path(path).open("rb") as handle:
        handle.seek(block.body_start_offset)
        raw = handle.read(block.body_end_offset - block.body_start_offset)
    return _normalize_newlines(raw.decode("utf-8", errors="replace"))


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
    _ensure_output_is_not_source(source, output)
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
                _normalize_newlines(_replace_connect_component(record.connect_line, new_component)),
                record.connect_line_end_offset,
            )

    with source.open("rb", buffering=_CHUNK_SIZE) as src, output.open("wb", buffering=_CHUNK_SIZE) as dst:
        cursor = 0
        pending_cr = False
        for start_offset in sorted(replacement_by_offset):
            replacement, end_offset = replacement_by_offset[start_offset]
            pending_cr = _copy_range(src, dst, cursor, start_offset, pending_cr)
            if pending_cr:
                dst.write(b"\n")
                pending_cr = False
            dst.write(replacement.encode("utf-8"))
            cursor = end_offset
        pending_cr = _copy_range(src, dst, cursor, None, pending_cr)
        if pending_cr:
            dst.write(b"\n")


def _ensure_output_is_not_source(source: Path, output: Path) -> None:
    if source.exists() and output.exists() and os.path.samefile(source, output):
        raise ValueError("Output path must differ from the source SPD path; writing would destroy the source.")
    if source.resolve() == output.resolve():
        raise ValueError("Output path must differ from the source SPD path; writing would destroy the source.")


def _copy_range(src, dst, start: int, end: int | None, pending_cr: bool) -> bool:
    """Copy [start, end) (or to EOF when end is None), LF-normalizing while streaming."""
    src.seek(start)
    remaining = None if end is None else end - start
    while remaining is None or remaining > 0:
        size = _CHUNK_SIZE if remaining is None else min(_CHUNK_SIZE, remaining)
        chunk = src.read(size)
        if not chunk:
            break
        if remaining is not None:
            remaining -= len(chunk)
        normalized, pending_cr = _normalize_chunk(chunk, pending_cr)
        dst.write(normalized)
    return pending_cr


def _normalize_chunk(chunk: bytes, pending_cr: bool) -> tuple[bytes, bool]:
    prefix = b""
    if pending_cr:
        if chunk[:1] == b"\n":
            chunk = chunk[1:]
        prefix = b"\n"
    trailing_cr = chunk.endswith(b"\r")
    if trailing_cr:
        chunk = chunk[:-1]
    normalized = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return prefix + normalized, trailing_cr


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
    return raw_line.decode("utf-8", errors="replace")


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
