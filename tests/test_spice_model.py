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


def test_prepare_model_rejects_nested_subckt() -> None:
    raw_model = (
        ".SUBCKT TOP P1 P2\n"
        "R1 P1 P2 1\n"
        ".SUBCKT CHILD A B\n"
        "R2 A B 2\n"
        ".ENDS CHILD\n"
        ".ENDS TOP\n"
    )

    with pytest.raises(ModelValidationError, match="hierarchical"):
        prepare_model_for_partialckt(raw_model, ["N1", "N2"])


def test_prepare_model_rejects_multiple_subckt_definitions() -> None:
    raw_model = (
        ".SUBCKT FIRST P1 P2\n"
        "R1 P1 P2 1\n"
        ".ENDS FIRST\n"
        ".SUBCKT SECOND A B\n"
        "R2 A B 2\n"
        ".ENDS SECOND\n"
    )

    with pytest.raises(ModelValidationError, match="hierarchical"):
        prepare_model_for_partialckt(raw_model, ["N1", "N2"])


def test_prepare_model_rejects_duplicate_subckt_ports() -> None:
    raw_model = ".SUBCKT DEV P1 P1 P2\nR1 P1 P2 1\n.ENDS DEV\n"

    with pytest.raises(ModelValidationError, match="duplicate port 'P1'"):
        prepare_model_for_partialckt(raw_model, ["A", "B", "C"])


def test_prepare_model_maps_ports_on_plus_continuation_without_space() -> None:
    raw_model = (
        ".SUBCKT DEV P1 P2 P3\n"
        "X1 P1 P2\n"
        "+P3 CHILD\n"
        ".ENDS DEV\n"
    )

    assert prepare_model_for_partialckt(raw_model, ["A", "B", "C"]) == "X1 A B\n+C CHILD\n"


def test_prepare_model_from_file_uses_utf8_lf_text(tmp_path: Path) -> None:
    model_path = tmp_path / "part.mod"
    model_path.write_text(
        "* source\n.SUBCKT CAP P1 P2\nC1 P1 P2 1u\n.ENDS CAP\n",
        encoding="utf-8",
        newline="\n",
    )

    prepared = prepare_model_for_partialckt(model_path.read_text(encoding="utf-8"), ["10", "20"])

    assert prepared == "* source\nC1 10 20 1u\n"


def test_prepare_model_maps_only_nodes_case_insensitively() -> None:
    raw_model = (
        "* PORT1 and 2 stay unchanged in comments\n"
        ".SUBCKT DEV Port1 2\n"
        "RPort1 PORT1 2 2\n"
        ".MODEL Port1 R(R=2)\n"
        "X1 port1 2 CHILD RVAL = 2\n"
        ".ENDS dev\n"
    )

    assert prepare_model_for_partialckt(raw_model, ["A", "B"]) == (
        "* PORT1 and 2 stay unchanged in comments\n"
        "RPort1 A B 2\n"
        ".MODEL Port1 R(R=2)\n"
        "X1 A B CHILD RVAL = 2\n"
    )


@pytest.mark.parametrize(
    ("raw_model", "message"),
    [
        (".SUBCKT DEV P N\nR1 P N 1\n", "missing its .ENDS"),
        (".SUBCKT DEV P N\nR1 P N 1\n.ENDS OTHER\n", "does not match"),
        (".SUBCKT DEV P p\nR1 P p 1\n.ENDS DEV\n", "duplicate port"),
        (".SUBCKT DEV P N rval=1\nR1 P N {rval}\n.ENDS DEV\n", "Parameterized .SUBCKT"),
        (".SUBCKT DEV P N\n.PARAM sense=V(P)\nR1 P N 1\n.ENDS DEV\n", r"V\(\)/I\(\) expressions"),
        (".SUBCKT DEV 1 2\n.PARAM sense=V( 3, 2 )\nR1 1 2 1\n.ENDS DEV\n", r"V\(\)/I\(\) expressions"),
        (".SUBCKT DEV P N\nQ1 MID P N MODEL\n.ENDS DEV\n", "Unsupported SPICE element"),
    ],
)
def test_prepare_model_rejects_unsafe_or_unsupported_models(raw_model: str, message: str) -> None:
    with pytest.raises(ModelValidationError, match=message):
        prepare_model_for_partialckt(raw_model, ["A", "B"])


def test_prepare_model_rejects_destination_shorts() -> None:
    raw_model = ".SUBCKT DEV P N\nR1 P MID 1\nR2 MID N 1\n.ENDS DEV\n"

    with pytest.raises(ModelValidationError, match="duplicate ExtNode"):
        prepare_model_for_partialckt(raw_model, ["A", "a"])
    with pytest.raises(ModelValidationError, match="collides with an internal SPICE node"):
        prepare_model_for_partialckt(raw_model, ["MID", "B"])
