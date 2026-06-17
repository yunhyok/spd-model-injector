from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Mapping, Sequence


_PARTIAL_RE = re.compile(r"^\.PartialCkt\s+(.+?)\s+ExtNode\s*=\s*(.*)$", re.IGNORECASE)


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


def scan_spd(path: str | Path) -> list[PartialCktBlock]:
    """Scan an SPD file for PartialCkt blocks without loading the file into memory."""
    spd_path = Path(path)
    blocks: list[PartialCktBlock] = []
    line_no = 0

    with spd_path.open("rb", buffering=1024 * 1024) as handle:
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            line_no += 1
            line_text = _decode_line(raw_line)
            if not line_text.startswith(".PartialCkt"):
                continue

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
                    if current_text.startswith(".EndPartialCkt"):
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

            blocks.append(
                PartialCktBlock(
                    component_name=_parse_component_name(header_lines[0]),
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

    return blocks


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
) -> None:
    """Write a new SPD, replacing only the body ranges for selected components."""
    source = Path(source_path)
    output = Path(output_path)
    replacement_by_offset = {
        block.body_start_offset: (_normalize_model_text(replacements[block.component_name]), block.body_end_offset)
        for block in blocks
        if block.component_name in replacements
    }

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


def _decode_line(raw_line: bytes) -> str:
    return raw_line.decode("utf-8")


def _strip_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_model_text(text: str) -> str:
    normalized = _normalize_newlines(text)
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
