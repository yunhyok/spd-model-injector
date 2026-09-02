from pathlib import Path

import pytest

from spd_model_injector.core import spd
from spd_model_injector.core.spd import PortRequest, read_block_body, read_connect_nodes, scan_spd, scan_spd_inventory, write_spd_with_replacements


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


def test_generate_port_resolves_selected_connect_and_inserts_before_endport(tmp_path: Path) -> None:
    source = tmp_path / "ports.spd"
    output = tmp_path / "ports_out.spd"
    source.write_text(
        ".PartialCkt CAP ExtNode = 1 2\n.Port\nC 1 2 1u\n.EndPartialCkt\n"
        ".Connect C2 CAP Checked = 1\n"
        "1 $Package.Node10!!1::VDD\n2 $Package.Node11!!2::DGND\n.EndC\n"
        ".Connect C3 CAP Checked = 1\n"
        "1 $Package.Node12!!1::VDD\n2 $Package.Node13!!2::DGND\n.EndC\n"
        ".Port\nPort7_C2_1::VDD Auto GenFromCktInstance=\"C2\" GenFromCktModel=\"DUT\"\n"
        "+            PositiveTerminal $Package.Node1!!1::VDD\n"
        "+            NegativeTerminal $Package.Node2!!2::DGND\n.EndPort\n"
        ".NetList\n VDD -> PowerNets Color = RED\n VDD_AUX Color = RED\n VDD_OFF::Unselected||DropShape Color = BLUE\n DGND -> GroundNets Color = BLUE Voltage = 0\n.EndNetList\n",
        encoding="utf-8",
        newline="\n",
    )
    inventory = scan_spd_inventory(source)
    record = inventory.refdes_records[0]
    assert record.connect_block_end_offset is not None
    assert record.unique_net_names == ("DGND", "VDD")
    assert record.net_node_counts == (("DGND", 1), ("VDD", 1))
    assert record.package_node_count == record.annotated_node_count == 2
    assert [(node.pin, node.net_name) for node in read_connect_nodes(source, record)] == [("1", "VDD"), ("2", "DGND")]
    assert inventory.max_port_number == 7
    assert inventory.existing_port_keys == (("C2", "VDD"),)
    assert inventory.power_nets == ("VDD", "VDD_AUX")
    assert "VDD_OFF" not in inventory.net_names
    write_spd_with_replacements(source, output, inventory.blocks, {}, refdes_records=inventory.refdes_records,
                                port_requests=[PortRequest("C3", "VDD", "DGND")], inventory=inventory)
    text = output.read_text(encoding="utf-8")
    assert "Port8_C3_1::VDD Auto GenFromCktInstance=\"C3\" GenFromCktModel=\"CAP\"" in text
    assert text.index("Port8_C3_1") < text.index(".EndPort")
    assert text.count(".EndPort") == 1


def test_existing_ports_can_be_inspected_disabled_enabled_and_deleted(tmp_path: Path) -> None:
    source = tmp_path / "manage-ports.spd"
    output = tmp_path / "manage-ports-out.spd"
    source.write_text(
        ".Connect U3 DUT Checked = 1\n"
        "A1 $Package.Node5!!A1::VDD\nG1 $Package.Node6!!G1::DGND\n.EndC\n"
        ".Port\n"
        "Port1_U1::VDD Auto GenFromCktInstance=\"U1\" GenFromCktModel=\"DUT\"\n"
        "+ PositiveTerminal $Package.Node1!!A1::VDD $Package.Node2!!A2::VDD\n"
        "+ NegativeTerminal $Package.Node3!!G1::DGND\n"
        "Port2_U2::VDD Disabled Auto GenFromCktInstance=\"U2\" GenFromCktModel=\"LGA\"\n"
        "+ PositiveTerminal $Package.Node4!!A1::VDD\n"
        "+ NegativeTerminal $Package.Node7!!G1::DGND $Package.Node8!!G2::DGND\n"
        ".EndPort\n"
        ".NetList\nVDD -> PowerNets\nDGND -> GroundNets\n.EndNetList\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)

    assert [(port.name, port.enabled, port.positive_node_count, port.negative_node_count)
            for port in inventory.port_records] == [
        ("Port1_U1::VDD", True, 2, 1),
        ("Port2_U2::VDD", False, 1, 2),
    ]
    write_spd_with_replacements(
        source,
        output,
        inventory.blocks,
        {},
        refdes_records=inventory.refdes_records,
        port_requests=[PortRequest("U3", "VDD", "DGND", enabled=False)],
        port_deletions=["Port1_U1::VDD"],
        port_enabled_changes={"Port2_U2::VDD": True},
        inventory=inventory,
    )

    text = output.read_text(encoding="utf-8")
    assert "Port1_U1::VDD" not in text
    assert "Port2_U2::VDD Auto" in text and "Port2_U2::VDD Disabled" not in text
    assert "Port3_U3_A1::VDD Disabled Auto" in text
    assert text.count(".EndPort") == 1


def test_generate_port_merges_all_matching_dut_pins(tmp_path: Path) -> None:
    source = tmp_path / "dut.spd"
    output = tmp_path / "dut-out.spd"
    source.write_text(
        ".Connect U1 DUT Checked = 1\n"
        "A1 $Package.Node10!!A1::VDD\nA2 $Package.Node2!!A2::VDD\n"
        "A3 $Package.Node7!!A3::VDD\nA4 $Package.Node5!!A4::VDD\nA5 $Package.Node12!!A5::VDD\n"
        "G1 $Package.Node11!!G1::DGND\nG2 $Package.Node4!!G2::DGND\n"
        "G3 $Package.Node8!!G3::DGND\nG4 $Package.Node1!!G4::DGND\n"
        "G5 $Package.Node9!!G5::DGND\nG6 $Package.Node6!!G6::DGND\n"
        "X1 $Package.Node3!!X1::AUX\n.EndC\n"
        ".Port\n.EndPort\n"
        ".NetList\nVDD -> PowerNets\nDGND -> GroundNets\nAUX Color = BLUE\n.EndNetList\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)

    assert inventory.refdes_records[0].net_node_counts == (("AUX", 1), ("DGND", 6), ("VDD", 5))
    write_spd_with_replacements(
        source,
        output,
        inventory.blocks,
        {},
        refdes_records=inventory.refdes_records,
        port_requests=[PortRequest("U1", "VDD", "DGND")],
        inventory=inventory,
    )

    text = output.read_text(encoding="utf-8")
    assert 'Port1_U1::VDD Auto GenFromCktInstance="U1" GenFromCktModel="DUT"' in text
    assert (
        "+            PositiveTerminal $Package.Node2!!A2::VDD $Package.Node5!!A4::VDD "
        "$Package.Node7!!A3::VDD $Package.Node10!!A1::VDD\n"
        "+                             $Package.Node12!!A5::VDD\n"
        "+            NegativeTerminal $Package.Node1!!G4::DGND $Package.Node4!!G2::DGND "
        "$Package.Node6!!G6::DGND $Package.Node8!!G3::DGND\n"
        "+                             $Package.Node9!!G5::DGND $Package.Node11!!G1::DGND\n"
    ) in text


def test_generate_port_batch_rejects_invalid_mapping_before_output_creation(tmp_path: Path) -> None:
    source = tmp_path / "invalid.spd"
    output = tmp_path / "invalid_out.spd"
    source.write_text(
        ".Connect C1 CAP Checked = 1\n1 $Package.Node1!!1::VDD\n.EndC\n"
        ".Port\nPort1_OLD::VDD Auto\n.EndPort\n"
        ".NetList\nVDD -> PowerNets Color = RED\nDGND -> GroundNets Color = BLUE\n.EndNetList\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)
    with pytest.raises(ValueError, match="does not map both selected NETs"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, refdes_records=inventory.refdes_records,
                                    port_requests=[PortRequest("C1", "VDD", "DGND")], inventory=inventory)
    assert not output.exists()


def test_generate_port_rejects_multiple_port_sections(tmp_path: Path) -> None:
    source = tmp_path / "multi.spd"
    output = tmp_path / "multi_out.spd"
    source.write_text(
        ".Connect C1 CAP Checked = 1\n1 $Package.Node1!!1::VDD\n2 $Package.Node2!!2::DGND\n.EndC\n"
        ".Port\n.EndPort\n.Port\n.EndPort\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)
    assert inventory.port_insertion_offset is None
    with pytest.raises(ValueError, match="safe .Port"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, refdes_records=inventory.refdes_records,
                                    port_requests=[PortRequest("C1", "VDD", "DGND")], inventory=inventory)
    assert not output.exists()


def test_generate_port_rejects_stale_port_metadata_without_touching_output(tmp_path: Path) -> None:
    source = tmp_path / "stale.spd"
    output = tmp_path / "stale_out.spd"
    source.write_text(
        ".Connect C1 CAP Checked = 1\n1 $Package.Node1!!1::VDD\n2 $Package.Node2!!2::DGND\n.EndC\n"
        ".Connect C2 CAP Checked = 1\n1 $Package.Node3!!1::VDD\n2 $Package.Node4!!2::DGND\n.EndC\n"
        ".Port\nPort7_C1_1::VDD Auto GenFromCktInstance=\"C1\"\n"
        "+ PositiveTerminal $Package.Node1!!1::VDD\n+ NegativeTerminal $Package.Node2!!2::DGND\n.EndPort\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)
    source.write_text(source.read_text(encoding="utf-8").replace("Port7_C1_1", "Port8_C1_1"), encoding="utf-8")
    output.write_text("sentinel", encoding="utf-8")
    with pytest.raises(ValueError, match="metadata changed"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, refdes_records=inventory.refdes_records,
                                    port_requests=[PortRequest("C2", "VDD", "DGND")], inventory=inventory)
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_generate_port_rejects_duplicate_package_node_base(tmp_path: Path) -> None:
    source = tmp_path / "duplicate-node.spd"
    output = tmp_path / "duplicate-node-out.spd"
    source.write_text(
        ".Connect C1 CAP Checked = 1\n1 $Package.Node1!!1::VDD\n2 $Package.Node1!!2::DGND\n.EndC\n"
        ".Port\nPort1_OLD::VDD Auto\n.EndPort\n"
        ".NetList\nVDD -> PowerNets\nDGND -> GroundNets\n.EndNetList\n",
        encoding="utf-8",
    )
    inventory = scan_spd_inventory(source)
    output.write_text("sentinel", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate Package.Node"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, refdes_records=inventory.refdes_records,
                                    port_requests=[PortRequest("C1", "VDD", "DGND")], inventory=inventory)
    assert output.read_text(encoding="utf-8") == "sentinel"
