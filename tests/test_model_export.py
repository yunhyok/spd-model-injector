from dataclasses import replace
from pathlib import Path

import pytest

from spd_model_injector.core import export_spice_model
from spd_model_injector.core import spd
from spd_model_injector.core.spd import scan_spd
from spd_model_injector.core.spice import prepare_model_for_partialckt


def scanned_model(tmp_path: Path, body: bytes = b"R1 P N 10\n", header: bytes = b"CAP ExtNode = P N"):
    source = tmp_path / "source.spd"
    source.write_bytes(b"Title\n.PartialCkt " + header + b"\n" + body + b".EndPartialCkt\nTail\n")
    return source, scan_spd(source)[0], tmp_path / "model.mod"


def test_export_preserves_body_and_continued_extnode_order(tmp_path: Path) -> None:
    body = (b"* original comment\r\n\r\n R1 Z A 1.20k ; retained\r\n"
            b".MODEL diode D(IS=1e-14)\r\n.PARAM gain=2\r\nB1 B Z V=V(A)*gain\r\n"
            b"+ arbitrary dialect continuation\r\n; retained too\r\n$ retained comment\r\n")
    source, block, output = scanned_model(tmp_path, body, b"CAP ExtNode = Z A\n+ B")
    original = source.read_bytes()

    export_spice_model(source, output, block)

    assert output.read_bytes() == (b"* Original SPD component: CAP\n.SUBCKT CAP Z A B\n"
                                   + body.replace(b"\r\n", b"\n") + b".ENDS CAP\n")
    assert source.read_bytes() == original


def test_export_round_trips_rlc_body_through_existing_converter(tmp_path: Path) -> None:
    body = b"* RLC network\nR1 P mid 10\nL1 mid N 1n\nC1 P N 2p\n"
    source, block, output = scanned_model(tmp_path, body)

    export_spice_model(source, output, block)

    prepared = prepare_model_for_partialckt(output.read_text(encoding="utf-8"), block.ext_nodes)
    assert prepared == "* Original SPD component: CAP\n" + body.decode()


@pytest.mark.parametrize("component,safe_name", [
    ("12V-cap.v1", "SPD_12V_cap_v1"),
    ("cap / 한글", "cap_____"),
    ("a;$*b", "a___b"),
])
def test_export_sanitizes_component_name(tmp_path: Path, component: str, safe_name: str) -> None:
    source, block, output = scanned_model(tmp_path, header=f"{component} ExtNode = P N".encode())
    export_spice_model(source, output, block)
    assert output.read_text(encoding="utf-8") == (
        f"* Original SPD component: {component}\n.SUBCKT {safe_name} P N\nR1 P N 10\n.ENDS {safe_name}\n"
    )


@pytest.mark.parametrize("body", [b"", b" \n\t\n", b"* comment\n ; comment\n\t$ comment\n"])
def test_export_rejects_empty_and_comment_only_models(tmp_path: Path, body: bytes) -> None:
    source, block, output = scanned_model(tmp_path, body)
    with pytest.raises(ValueError, match="empty|comments"):
        export_spice_model(source, output, block)
    assert not output.exists()


@pytest.mark.parametrize("marker", [".SUBCKT nested P N", ".ENDS nested", ".END", ".end; comment",
                                     " .PartialCkt nested ExtNode = P N", " .EndPartialCkt"])
def test_export_rejects_body_structure_markers(tmp_path: Path, marker: str) -> None:
    source, block, output = scanned_model(tmp_path, f"R1 P N 10\n{marker}\n".encode())
    with pytest.raises(ValueError, match="structural"):
        export_spice_model(source, output, block)
    assert not output.exists()


@pytest.mark.parametrize("header", [b"CAP", b"CAP ExtNode =", b"CAP ExtNode = P p",
                                    b"CAP ExtNode = P N ; comment", b"CAP ExtNode = P N PARAMS:x=1",
                                    b"CAP ExtNode = P (N)", b"CAP ExtNode = P $comment"])
def test_export_rejects_malformed_header_and_nodes(tmp_path: Path, header: bytes) -> None:
    source, block, output = scanned_model(tmp_path, header=header)
    with pytest.raises(ValueError, match="header|ExtNode"):
        export_spice_model(source, output, block)
    assert not output.exists()


def test_export_rejects_malformed_end_marker(tmp_path: Path) -> None:
    source = tmp_path / "source.spd"
    source.write_bytes(b".PartialCkt CAP ExtNode = P N\nR1 P N 10\n.EndPartialCkt garbage\n")
    block = scan_spd(source)[0]
    with pytest.raises(ValueError, match="end marker"):
        export_spice_model(source, tmp_path / "model.mod", block)


@pytest.mark.parametrize("changes", [
    {"source_signature": None}, {"clone_source_name": "CAP"}, {"source_component_name": "CAP"},
    {"component_name": "renamed"}, {"ext_nodes": ["N", "P"]}, {"body_start_offset": -1},
])
def test_export_requires_original_scanned_metadata(tmp_path: Path, changes: dict) -> None:
    source, block, output = scanned_model(tmp_path)
    with pytest.raises(ValueError):
        export_spice_model(source, output, replace(block, **changes))
    assert not output.exists()


def test_export_rejects_source_changed_since_scan(tmp_path: Path) -> None:
    source, block, output = scanned_model(tmp_path)
    source.write_bytes(b"Added line\n" + source.read_bytes())
    with pytest.raises(ValueError, match="changed since scan"):
        export_spice_model(source, output, block)
    assert not output.exists()


@pytest.mark.parametrize("hardlink", [False, True])
def test_export_protects_source_and_hardlink(tmp_path: Path, hardlink: bool) -> None:
    source, block, output = scanned_model(tmp_path)
    original = source.read_bytes()
    if hardlink:
        output.hardlink_to(source)
    else:
        output = source
    with pytest.raises(ValueError, match="differ from the source"):
        export_spice_model(source, output, block)
    assert source.read_bytes() == original


def test_export_rejects_invalid_utf8_without_replacement_characters(tmp_path: Path) -> None:
    source, block, output = scanned_model(tmp_path, b"* bad comment \xff\nR1 P N 10\n")
    with pytest.raises(UnicodeDecodeError):
        export_spice_model(source, output, block)
    assert not output.exists()


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_export_failure_preserves_existing_output_and_cleans_temp(tmp_path: Path, monkeypatch, stage: str) -> None:
    source, block, output = scanned_model(tmp_path)
    output.write_bytes(b"previous model")

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(spd.os, stage, fail)
    with pytest.raises(OSError, match="disk failure"):
        export_spice_model(source, output, block)
    assert output.read_bytes() == b"previous model"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["model.mod", "source.spd"]


@pytest.mark.parametrize("stage", ["read", "write"])
def test_export_rechecks_source_after_read_and_write(tmp_path: Path, monkeypatch, stage: str) -> None:
    source, block, output = scanned_model(tmp_path)
    output.write_bytes(b"previous model")
    original_signature = spd._source_signature
    original_fsync = spd.os.fsync
    calls = 0

    def signature(path):
        nonlocal calls
        calls += 1
        if stage == "read" and calls == 2:
            source.write_bytes(source.read_bytes() + b"Changed during read\n")
        return original_signature(path)

    def fsync(fd):
        original_fsync(fd)
        if stage == "write":
            source.write_bytes(source.read_bytes() + b"Changed during write\n")

    monkeypatch.setattr(spd, "_source_signature", signature)
    monkeypatch.setattr(spd.os, "fsync", fsync)
    with pytest.raises(ValueError, match="changed during export"):
        export_spice_model(source, output, block)
    assert output.read_bytes() == b"previous model"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["model.mod", "source.spd"]
