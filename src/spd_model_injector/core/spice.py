from __future__ import annotations

import re
from typing import Sequence


class ModelValidationError(ValueError):
    pass


_SUBCKT_RE = re.compile(r"^\.SUBCKT\s+(\S+)\s*(.*)$", re.IGNORECASE)
_ENDS_RE = re.compile(r"^\.ENDS\b", re.IGNORECASE)


def prepare_model_for_partialckt(raw_model: str, ext_nodes: Sequence[str]) -> str:
    normalized = _normalize_newlines(raw_model)
    lines = normalized.splitlines()
    subckt_index, subckt_ports, header_end_index = _find_subckt(lines)

    if len(subckt_ports) != len(ext_nodes):
        raise ModelValidationError(
            f"SPICE model port count ({len(subckt_ports)}) does not match PartialCkt port count ({len(ext_nodes)})."
        )

    port_map = dict(zip(subckt_ports, ext_nodes, strict=True))
    output_lines: list[str] = []
    output_lines.extend(lines[:subckt_index])

    for line in lines[header_end_index + 1 :]:
        if _ENDS_RE.match(line.strip()):
            break
        output_lines.append(_replace_node_tokens(line, port_map))

    return "\n".join(output_lines).rstrip("\n") + "\n" if output_lines else ""


def _find_subckt(lines: Sequence[str]) -> tuple[int, list[str], int]:
    for index, line in enumerate(lines):
        match = _SUBCKT_RE.match(line.strip())
        if not match:
            continue
        ports = [token for token in match.group(2).split() if token]
        header_end = index
        next_index = index + 1
        while next_index < len(lines) and lines[next_index].lstrip().startswith("+"):
            ports.extend(lines[next_index].lstrip()[1:].split())
            header_end = next_index
            next_index += 1
        return index, ports, header_end
    raise ModelValidationError("SPICE model does not contain a .SUBCKT header.")


def _replace_node_tokens(line: str, port_map: dict[str, str]) -> str:
    if not port_map or line.lstrip().startswith("*"):
        return line

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return port_map.get(token, token)

    return re.sub(r"(?<![A-Za-z0-9_.$+-])[A-Za-z0-9_.$+-]+(?![A-Za-z0-9_.$+-])", replace, line)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
