from pathlib import Path

from spd_model_injector.core.spd import read_block_body, scan_spd, scan_spd_inventory, write_spd_with_replacements


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


def test_scan_spd_reports_progress_while_searching_large_files(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        "Title\n"
        + ("* filler line\n" * 2000)
        + ".PartialCkt C1 ExtNode =  1 2\n"
        + "C 1 2 1u\n"
        + ".EndPartialCkt\n",
        encoding="utf-8",
        newline="\n",
    )
    events: list[tuple[str, int, int]] = []

    blocks = scan_spd(spd_path, progress_callback=lambda message, current, total: events.append((message, current, total)))

    assert [block.component_name for block in blocks] == ["C1"]
    assert events[0][0] == "Opening SPD file"
    assert any(message.startswith("Found C1") for message, _, _ in events)
    assert events[-1][0] == "Scan complete: 1 PartialCkt blocks"
    assert events[-1][1] == events[-1][2] == spd_path.stat().st_size

def test_scan_spd_does_not_decode_partialckt_body_lines(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_bytes(
        b"Title\n"
        b".PartialCkt C1 ExtNode =  1 2\n"
        b"\xff\xfe body bytes only need offset scanning\n"
        b".EndPartialCkt\n"
        b"Tail\n"
    )

    blocks = scan_spd(spd_path)

    assert [block.component_name for block in blocks] == ["C1"]
    assert blocks[0].body_end_offset > blocks[0].body_start_offset


def test_scan_spd_inventory_reads_refdes_activation_statuses(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        "Title Example\n"
        ".PartialCkt CAP_0402 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".PartialCkt RES_0402 ExtNode =  A B\n"
        "R A B 1\n"
        ".EndPartialCkt\n"
        ".Connect C100_0 CAP_0402 Checked = 1\n"
        ".Connect C285_0 CAP_0402 Usage = 0b1000 Checked = 1\n"
        ".Connect C104_14 CAP_0402 Usage = 0b111000 Checked = 1\n"
        ".Connect R1 RES_0402 Usage = 0b1010 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )

    inventory = scan_spd_inventory(spd_path)

    assert [block.component_name for block in inventory.blocks] == ["CAP_0402", "RES_0402"]
    assert [(record.component_name, record.refdes_name, record.activation_status) for record in inventory.refdes_records] == [
        ("CAP_0402", "C100_0", "Automatic"),
        ("CAP_0402", "C285_0", "Enabled"),
        ("CAP_0402", "C104_14", "Disabled"),
        ("RES_0402", "R1", "Unknown"),
    ]
    assert [record.refdes_name for record in inventory.refdes_by_component["CAP_0402"]] == [
        "C100_0",
        "C285_0",
        "C104_14",
    ]
    assert [record.refdes_name for record in inventory.refdes_by_component["RES_0402"]] == ["R1"]


def test_scan_spd_inventory_records_connect_line_locations(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        "Title\n"
        ".Connect C100_0 CAP_0402 Usage = 0b1000 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )

    inventory = scan_spd_inventory(spd_path)

    record = inventory.refdes_records[0]
    assert record.connect_line_start_offset == len("Title\n")
    assert record.connect_line_end_offset == spd_path.stat().st_size
    assert record.connect_line == ".Connect C100_0 CAP_0402 Usage = 0b1000 Checked = 1\n"


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


def test_write_spd_replaces_refdes_component_and_preserves_connect_attributes(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        "Title Example\n"
        ".PartialCkt CAP_0402 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".PartialCkt CAP_0603 ExtNode =  1 2\n"
        "C 1 2 2u\n"
        ".EndPartialCkt\n"
        ".Connect C100_0 CAP_0402 Usage = 0b1000 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    inventory = scan_spd_inventory(spd_path)

    write_spd_with_replacements(
        spd_path,
        output_path,
        inventory.blocks,
        {},
        refdes_component_changes={"C100_0": "CAP_0603"},
        refdes_records=inventory.refdes_records,
    )

    assert ".Connect C100_0 CAP_0603 Usage = 0b1000 Checked = 1\n" in output_path.read_text(encoding="utf-8")


def test_write_spd_combines_partialckt_body_and_refdes_component_changes(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        "Title Example\n"
        ".PartialCkt CAP_0402 ExtNode =  1 2\n"
        "C 1 2 1u\n"
        ".EndPartialCkt\n"
        ".PartialCkt CAP_0603 ExtNode =  1 2\n"
        "C 1 2 2u\n"
        ".EndPartialCkt\n"
        ".Connect C100_0 CAP_0402 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    inventory = scan_spd_inventory(spd_path)

    write_spd_with_replacements(
        spd_path,
        output_path,
        inventory.blocks,
        {"CAP_0402": "* replaced body\n"},
        refdes_component_changes={"C100_0": "CAP_0603"},
        refdes_records=inventory.refdes_records,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "Title Example\n"
        ".PartialCkt CAP_0402 ExtNode =  1 2\n"
        "* replaced body\n"
        ".EndPartialCkt\n"
        ".PartialCkt CAP_0603 ExtNode =  1 2\n"
        "C 1 2 2u\n"
        ".EndPartialCkt\n"
        ".Connect C100_0 CAP_0603 Checked = 1\n"
    )
