from __future__ import annotations

import re
from typing import Sequence


class ModelValidationError(ValueError):
    pass


_SUBCKT_RE = re.compile(r"^\.SUBCKT\s+(\S+)\s*(.*)$", re.IGNORECASE)
_ENDS_RE = re.compile(r"^\.ENDS(?:\s+((?![$;])\S+))?(?:\s+[$;].*)?\s*$", re.IGNORECASE)
_FIXED_NODE_COUNTS = {"C": 2, "L": 2, "R": 2}
_NON_TOPOLOGY_DIRECTIVES = {".func", ".include", ".lib", ".model", ".options", ".param", ".temp"}
_TOKEN_CHARS = r"A-Za-z0-9_.$+-"
_SPICE_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?:t|g|meg|k|mil|m|u|n|p|f)?$",
    re.IGNORECASE,
)


def prepare_model_for_partialckt(raw_model: str, ext_nodes: Sequence[str]) -> str:
    normalized = _normalize_newlines(raw_model)
    lines = normalized.splitlines()
    subckt_index, subckt_name, subckt_ports, header_end_index = _find_subckt(lines)

    if len(subckt_ports) != len(ext_nodes):
        raise ModelValidationError(
            f"SPICE model port count ({len(subckt_ports)}) does not match PartialCkt port count ({len(ext_nodes)})."
        )

    duplicate_port = _first_duplicate(subckt_ports)
    if duplicate_port is not None:
        raise ModelValidationError(f"SPICE model .SUBCKT defines duplicate port '{duplicate_port}'.")
    duplicate_ext_node = _first_duplicate(ext_nodes)
    if duplicate_ext_node is not None:
        raise ModelValidationError(f"PartialCkt defines duplicate ExtNode '{duplicate_ext_node}'.")
    if any(not node or any(char.isspace() for char in node) for node in ext_nodes):
        raise ModelValidationError("PartialCkt ExtNode values must be non-empty single tokens.")

    port_map = {port.casefold(): node for port, node in zip(subckt_ports, ext_nodes, strict=True)}
    output_lines: list[str] = []
    output_lines.extend(lines[:subckt_index])

    end_index = _find_ends(lines, header_end_index + 1, subckt_name)
    prepared_body: list[str] = []
    internal_nodes: set[str] = set()
    for statement in _body_statements(lines[header_end_index + 1 : end_index]):
        prepared, nodes = _prepare_statement(statement, port_map)
        prepared_body.extend(prepared)
        internal_nodes.update(node.casefold() for node in nodes if node.casefold() not in port_map)

    collision = next((node for node in ext_nodes if node.casefold() in internal_nodes), None)
    if collision is not None:
        raise ModelValidationError(
            f"PartialCkt ExtNode '{collision}' collides with an internal SPICE node; mapping would short distinct nodes."
        )
    output_lines.extend(prepared_body)

    return "\n".join(output_lines).rstrip("\n") + "\n" if output_lines else ""


def _find_subckt(lines: Sequence[str]) -> tuple[int, str, list[str], int]:
    for index, line in enumerate(lines):
        match = _SUBCKT_RE.match(line.strip())
        if not match:
            continue
        tokens = _tokens_before_comment(match.group(2))
        header_end = index
        next_index = index + 1
        while next_index < len(lines) and lines[next_index].lstrip().startswith("+"):
            tokens.extend(_tokens_before_comment(lines[next_index].lstrip()[1:]))
            header_end = next_index
            next_index += 1
        return index, match.group(1), _ports_before_parameters(tokens), header_end
    raise ModelValidationError("SPICE model does not contain a .SUBCKT header.")


def _find_ends(lines: Sequence[str], start: int, subckt_name: str) -> int:
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if _SUBCKT_RE.match(stripped):
            raise ModelValidationError("hierarchical / multiple .SUBCKT models are not supported.")
        match = _ENDS_RE.match(stripped)
        if match:
            end_name = match.group(1)
            if end_name is not None and end_name.casefold() != subckt_name.casefold():
                raise ModelValidationError(
                    f"SPICE model .ENDS name '{end_name}' does not match .SUBCKT name '{subckt_name}'."
                )
            for trailing in lines[index + 1 :]:
                if _SUBCKT_RE.match(trailing.strip()):
                    raise ModelValidationError("hierarchical / multiple .SUBCKT models are not supported.")
            return index
        if stripped.upper().startswith(".ENDS"):
            raise ModelValidationError(f"Invalid SPICE .ENDS line: {stripped}")
    raise ModelValidationError(f"SPICE model .SUBCKT '{subckt_name}' is missing its .ENDS line.")


def _ports_before_parameters(tokens: Sequence[str]) -> list[str]:
    parameter_index = _parameter_index(tokens)
    if parameter_index != len(tokens):
        raise ModelValidationError("Parameterized .SUBCKT headers are not supported.")
    return list(tokens)


def _parameter_index(tokens: Sequence[str], start: int = 0) -> int:
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.casefold().startswith("params:") or "=" in token or (
            index + 1 < len(tokens) and tokens[index + 1] == "="
        ):
            return index
    return len(tokens)


def _tokens_before_comment(text: str) -> list[str]:
    tokens: list[str] = []
    for token in text.split():
        if token == "$" or token.startswith(";"):
            break
        tokens.append(token)
    return tokens


def _first_duplicate(items: Sequence[str]) -> str | None:
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if key in seen:
            return item
        seen.add(key)
    return None


def _body_statements(lines: Sequence[str]) -> list[list[str]]:
    statements: list[list[str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if stripped.startswith("+"):
            raise ModelValidationError("SPICE continuation line has no preceding statement.")
        statement = [line]
        index += 1
        if stripped and not stripped.startswith("*"):
            while index < len(lines) and lines[index].lstrip().startswith("+"):
                statement.append(lines[index])
                index += 1
        statements.append(statement)
    return statements


def _prepare_statement(statement: Sequence[str], port_map: dict[str, str]) -> tuple[list[str], list[str]]:
    first = statement[0].lstrip()
    if not first or first.startswith("*"):
        return list(statement), []
    tokens = _statement_tokens(statement)
    if not tokens:
        return list(statement), []
    if re.search(r"\b[VI]\s*\(", " ".join(token[3] for token in tokens[1:]), re.IGNORECASE):
        raise ModelValidationError("SPICE V()/I() expressions in .SUBCKT bodies are not supported.")

    first_token = tokens[0][3]
    if first_token.startswith("."):
        if first_token.casefold() not in _NON_TOPOLOGY_DIRECTIVES:
            raise ModelValidationError(f"Unsupported SPICE directive in .SUBCKT body: {first_token}")
        if any(_unsafe_non_node_reference(token[3], port_map) for token in tokens[1:]):
            raise ModelValidationError(
                f"SPICE directive '{first_token}' references a .SUBCKT port and cannot be mapped safely."
            )
        return list(statement), []

    device = first_token[0].upper()
    if device in _FIXED_NODE_COUNTS:
        node_count = _FIXED_NODE_COUNTS[device]
        if len(tokens) < node_count + 2:
            raise ModelValidationError(f"Invalid or incomplete SPICE '{device}' element: {statement[0].strip()}")
        node_indexes = set(range(1, node_count + 1))
    elif device == "X":
        token_text = [token[3] for token in tokens]
        parameter_index = _parameter_index(token_text, 1)
        model_index = parameter_index - 1
        if model_index < 2:
            raise ModelValidationError(f"Invalid or incomplete SPICE 'X' element: {statement[0].strip()}")
        node_indexes = set(range(1, model_index))
    elif device == "K":
        node_indexes = set()
    else:
        raise ModelValidationError(f"Unsupported SPICE element type in .SUBCKT body: '{device}'.")

    for index, (_, _, _, token) in enumerate(tokens):
        if index in node_indexes or index == 0:
            continue
        if _unsafe_non_node_reference(token, port_map):
            raise ModelValidationError(
                f"SPICE expression references .SUBCKT port '{token}' outside a supported node field."
            )

    replacements: dict[int, list[tuple[int, int, str]]] = {}
    nodes: list[str] = []
    for index in sorted(node_indexes):
        line_index, start, end, token = tokens[index]
        nodes.append(token)
        mapped = port_map.get(token.casefold())
        if mapped is not None:
            replacements.setdefault(line_index, []).append((start, end, mapped))
        elif _contains_port_reference(token, port_map):
            raise ModelValidationError(f"Unsupported SPICE node token syntax: {token}")

    prepared = list(statement)
    for line_index, edits in replacements.items():
        for start, end, replacement in sorted(edits, reverse=True):
            prepared[line_index] = prepared[line_index][:start] + replacement + prepared[line_index][end:]
    return prepared, nodes


def _statement_tokens(statement: Sequence[str]) -> list[tuple[int, int, int, str]]:
    tokens: list[tuple[int, int, int, str]] = []
    for line_index, line in enumerate(statement):
        stripped = line.lstrip()
        start = len(line) - len(stripped)
        if line_index:
            start += 1
        for match in re.finditer(r"\S+", line[start:]):
            token = match.group(0)
            if token == "$" or token.startswith(";"):
                break
            tokens.append((line_index, start + match.start(), start + match.end(), token))
    return tokens


def _unsafe_non_node_reference(text: str, port_map: dict[str, str]) -> bool:
    if text.casefold() in port_map or _is_spice_number_value(text):
        return False
    return any(
        not _SPICE_NUMBER_RE.fullmatch(port) and _contains_port_reference(text, {port: ""})
        for port in port_map
    )


def _is_spice_number_value(text: str) -> bool:
    value = text.split("=", 1)[-1].strip("{}'\"")
    return bool(_SPICE_NUMBER_RE.fullmatch(value))


def _contains_port_reference(text: str, port_map: dict[str, str]) -> bool:
    return any(
        re.search(rf"(?<![{_TOKEN_CHARS}]){re.escape(port)}(?![{_TOKEN_CHARS}])", text, re.IGNORECASE)
        for port in port_map
    )


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
