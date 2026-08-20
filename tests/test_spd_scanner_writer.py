from pathlib import Path

import pytest

from spd_model_injector.core import spd
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
    assert events[-1][0] == "Scan complete: 1 PartialCkt blocks, 0 RefDes records"
    assert events[-1][1] == events[-1][2] == spd_path.stat().st_size


def test_scan_spd_handles_non_utf8_partialckt_body_lines(tmp_path: Path) -> None:
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


def test_scan_spd_inventory_reads_primary_net_name_from_connect_node_lines(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    spd_path.write_text(
        ".Connect C100_0 CAP_0402 Checked = 1\n"
        "1 $Package.Node6430!!1::5V_A\n"
        "2 $Package.Node13541!!2::GND\n"
        ".EndC\n"
        ".Connect C101_0 CAP_0402 Checked = 1\n"
        "1 $Package.Node7!!1\n"
        ".EndC\n",
        encoding="utf-8",
        newline="\n",
    )

    inventory = scan_spd_inventory(spd_path)

    assert [(record.refdes_name, record.net_name) for record in inventory.refdes_records] == [
        ("C100_0", "5V_A"),
        ("C101_0", ""),
    ]


def test_scan_spd_inventory_uses_first_net_bearing_pin_after_unannotated_pin(tmp_path: Path) -> None:
    spd_path = tmp_path / "late-net.spd"
    spd_path.write_text(
        ".Connect U1 DUT Checked = 1\n"
        "A1 $Package.Node1!!A1\n"
        "A2 $Package.Node2!!A2::VDD\n"
        "A3 $Package.Node3!!A3::GND\n"
        ".EndC\n",
        encoding="utf-8",
        newline="\n",
    )

    inventory = scan_spd_inventory(spd_path)

    assert inventory.refdes_records[0].net_name == "VDD"


def test_scan_spd_inventory_reads_primary_net_name_from_alphanumeric_pin(tmp_path: Path) -> None:
    spd_path = tmp_path / "lga.spd"
    spd_path.write_text(
        ".Connect LGA LGA-ALL Usage = 0b1000 Checked = 1\n"
        "A10 $Package.Node20666!!A10::DGND\n"
        "AA1 $Package.Node20667!!AA1::VDD\n"
        ".EndC\n",
        encoding="utf-8",
        newline="\n",
    )

    inventory = scan_spd_inventory(spd_path)

    assert inventory.refdes_records[0].net_name == "DGND"


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


def test_write_spd_replaces_refdes_activation_status_and_preserves_connect_attributes(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        "Title Example\n"
        ".Connect C100_0 CAP_0402 Checked = 1\n"
        ".Connect C285_0 CAP_0402 Usage = 0b1000 Checked = 1\n"
        ".Connect C104_14 CAP_0402 Usage = 0b111000 Checked = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    inventory = scan_spd_inventory(spd_path)

    write_spd_with_replacements(
        spd_path,
        output_path,
        inventory.blocks,
        {},
        refdes_activation_status_changes={
            "C100_0": "Enabled",
            "C285_0": "Disabled",
            "C104_14": "Automatic",
        },
        refdes_records=inventory.refdes_records,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "Title Example\n"
        ".Connect C100_0 CAP_0402 Usage = 0b1000 Checked = 1\n"
        ".Connect C285_0 CAP_0402 Usage = 0b111000 Checked = 1\n"
        ".Connect C104_14 CAP_0402 Checked = 1\n"
    )


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


def test_write_spd_combines_refdes_component_and_activation_status_changes(tmp_path: Path) -> None:
    spd_path = tmp_path / "board.spd"
    output_path = tmp_path / "board_out.spd"
    spd_path.write_text(
        "Title Example\n"
        ".Connect C100_0 CAP_0402 Checked = 1\n",
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
        refdes_activation_status_changes={"C100_0": "Disabled"},
        refdes_records=inventory.refdes_records,
    )

    assert output_path.read_text(encoding="utf-8") == (
        "Title Example\n"
        ".Connect C100_0 CAP_0603 Usage = 0b111000 Checked = 1\n"
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
