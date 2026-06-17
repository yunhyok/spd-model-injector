from pathlib import Path

import pytest

from spd_model_injector.core.spice import ModelValidationError, prepare_model_for_partialckt


def test_prepare_model_preserves_comments_and_maps_subckt_ports_to_extnodes() -> None:
    raw_model = (
        "* vendor line\n"
        "* condition line\n"
        ".SUBCKT GA342A1XGD330JW31 Port1 Port2\n"
        "C01 Port1 N01 3.30e-11\n"
        "R01 Port1 N01 6.00e+09\n"
        "C04 N01 Port2 9.68e-13\n"
        ".ENDS GA342A1XGD330JW31\n"
    )

    prepared = prepare_model_for_partialckt(raw_model, ["1", "2"])

    assert prepared == (
        "* vendor line\n"
        "* condition line\n"
        "C01 1 N01 3.30e-11\n"
        "R01 1 N01 6.00e+09\n"
        "C04 N01 2 9.68e-13\n"
    )


def test_prepare_model_maps_numeric_like_vendor_port_names_without_partial_replacement() -> None:
    raw_model = (
        ".SUBCKT DEVICE P01 P02 P03\n"
        "X1 P01 N_P01 P02 CHILD\n"
        "R1 N_P01 P03 1\n"
        ".ENDS DEVICE\n"
    )

    prepared = prepare_model_for_partialckt(raw_model, ["A", "B", "C"])

    assert prepared == "X1 A N_P01 B CHILD\nR1 N_P01 C 1\n"


def test_prepare_model_supports_subckt_header_continuation() -> None:
    raw_model = (
        ".SUBCKT MANY P1 P2\n"
        "+ P3 P4\n"
        "R1 P1 P4 1\n"
        ".ENDS MANY\n"
    )

    assert prepare_model_for_partialckt(raw_model, ["N1", "N2", "N3", "N4"]) == "R1 N1 N4 1\n"


def test_prepare_model_rejects_port_count_mismatch() -> None:
    raw_model = ".SUBCKT CAP 1 2 3\nC1 1 2 1u\n.ENDS CAP\n"

    with pytest.raises(ModelValidationError, match="port count"):
        prepare_model_for_partialckt(raw_model, ["1", "2"])


def test_prepare_model_from_file_uses_utf8_lf_text(tmp_path: Path) -> None:
    model_path = tmp_path / "part.mod"
    model_path.write_text(
        "* source\n.SUBCKT CAP P1 P2\nC1 P1 P2 1u\n.ENDS CAP\n",
        encoding="utf-8",
        newline="\n",
    )

    prepared = prepare_model_for_partialckt(model_path.read_text(encoding="utf-8"), ["10", "20"])

    assert prepared == "* source\nC1 10 20 1u\n"
