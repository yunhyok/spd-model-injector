from pathlib import Path

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
