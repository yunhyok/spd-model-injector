from pathlib import Path

import pytest

from spd_model_injector.core import spd
from spd_model_injector.core.spd import read_block_body, scan_spd, write_spd_with_replacements


def test_scan_spd_reads_partialckt_blocks_and_extnode_continuations(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        "Title Example\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".PartialCkt U1 ExtNode =  LGA_A1 LGA_A2\n"
        "+ LGA_A3 LGA_A4\n"
        "R LGA_A1 LGA_A2 1\n"
        ".EndPartialCkt\n"
        "Tail\n",
        encoding="utf-8",
        newline="\n",
    )

    blocks = scan_spd(spd_path)

    assert [block.component_name for block in blocks] == ["C1", "U1"]
    assert blocks[0].ext_nodes == ["1", "2"]
    assert blocks[1].ext_nodes == ["LGA_A1", "LGA_A2", "LGA_A3", "LGA_A4"]
    assert read_block_body(spd_path, blocks[1]) == "R LGA_A1 LGA_A2 1\n"


def test_write_spd_replaces_only_changed_partialckt_bodies_and_keeps_lf(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        "Title Example\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".PartialCkt U1 ExtNode =  A B C\n"
        "+ D\n"
        "R A B 1\n"
        ".EndPartialCkt\n",
        encoding="utf-8",
        newline="\n",
    )
    blocks = scan_spd(spd_path)

    write_spd_with_replacements(
        spd_path,
        output_path,
        blocks,
        {"C1": "* source preserved\nC01 1 N01 3.3e-11\nR01 N01 2 0.1\n"},
    )

    output_bytes = output_path.read_bytes()
    assert b"\r\n" not in output_bytes
    assert output_path.read_text(encoding="utf-8") == (
        "Title Example\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "* source preserved\n"
        "C01 1 N01 3.3e-11\n"
        "R01 N01 2 0.1\n"
        ".EndPartialCkt\n"
        ".PartialCkt U1 ExtNode =  A B C\n"
        "+ D\n"
        "R A B 1\n"
        ".EndPartialCkt\n"
    )


def test_write_spd_refuses_to_overwrite_source(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    original = (
        "Title Example\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
    )
    spd_path.write_text(original, encoding="utf-8", newline="\n")
    blocks = scan_spd(spd_path)

    with pytest.raises(ValueError, match="differ from the source"):
        write_spd_with_replacements(spd_path, spd_path, blocks, {"C1": "C01 1 2 1p\n"})

    with pytest.raises(ValueError, match="differ from the source"):
        write_spd_with_replacements(spd_path, tmp_path / "." / "board.spd", blocks, {"C1": "C01 1 2 1p\n"})

    assert spd_path.read_text(encoding="utf-8") == original


def test_write_spd_normalizes_crlf_source_to_lf(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_bytes(
        b"Title Example\r\n"
        b".PartialCkt C1 ExtNode =  1 2\r\n"
        b"C 1 2 1u\r\n"
        b".EndPartialCkt\r\n"
        b".PartialCkt U1 ExtNode =  A B C\r\n"
        b"+ D\r\n"
        b"R A B 1\r\n"
        b".EndPartialCkt\r\n"
    )
    blocks = scan_spd(spd_path)

    write_spd_with_replacements(spd_path, output_path, blocks, {"C1": "* new\r\nC01 1 2 1p\r\n"})

    output_bytes = output_path.read_bytes()
    assert b"\r" not in output_bytes
    assert output_path.read_text(encoding="utf-8") == (
        "Title Example\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "* new\n"
        "C01 1 2 1p\n"
        ".EndPartialCkt\n"
        ".PartialCkt U1 ExtNode =  A B C\n"
        "+ D\n"
        "R A B 1\n"
        ".EndPartialCkt\n"
    )


def test_write_spd_normalizes_crlf_across_chunk_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    # "ABC\r" fills a 4-byte chunk exactly, so the \r\n straddles the chunk boundary.
    spd_path.write_bytes(b"ABC\r\nDEF\r\nGHI\r\n")
    monkeypatch.setattr(spd, "_CHUNK_SIZE", 4)

    write_spd_with_replacements(spd_path, output_path, [], {})

    assert output_path.read_bytes() == b"ABC\nDEF\nGHI\n"


def test_scan_spd_tolerates_non_utf8_bytes(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_bytes(
        b"* vendor \xff comment\n"
        b".PartialCkt C1 ExtNode =  1 2\n"
        b"C 1 \xff 2 1u\n"
        b".EndPartialCkt\n"
    )

    blocks = scan_spd(spd_path)

    assert [block.component_name for block in blocks] == ["C1"]
    assert blocks[0].ext_nodes == ["1", "2"]
    assert read_block_body(spd_path, blocks[0]).startswith("C 1 ")


def test_scan_spd_handles_mixed_case_markers_and_word_boundaries(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        ".PARTIALCKT C1 EXTNODE =  1 2\n"
        ".EndPartialCktExtra keep-me\n"
        "C 1 2 1u\n"
        ".endpartialckt\n"
        ".PartialCktX NOTABLOCK\n"
        "Tail\n",
        encoding="utf-8",
        newline="\n",
    )

    blocks = scan_spd(spd_path)

    assert [block.component_name for block in blocks] == ["C1"]
    assert blocks[0].ext_nodes == ["1", "2"]
    body = read_block_body(spd_path, blocks[0])
    assert ".EndPartialCktExtra keep-me" in body
    assert "C 1 2 1u" in body
