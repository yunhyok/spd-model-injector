import os
from dataclasses import replace
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from spd_model_injector.core.spd import scan_spd_inventory, write_spd_with_replacements
from spd_model_injector.ui.main_window import MainWindow


def _board() -> str:
    return (
        "* ComponentDefinition description lines\n"
        ".Part C1 W=1\n"
        "+ description=source\n"
        ".Part C2 W=2\n"
        ".PartialCkt C1 ExtNode =  1 2\n"
        "R 1 2 1\n"
        ".EndPartialCkt\n"
        ".PartialCkt C2 ExtNode =  1 2\n"
        "R 1 2 2\n"
        ".EndPartialCkt\n"
        ".Connect X1 C1 Usage = 0b1000 Checked = 1\n"
        ".Connect X2 C2 Usage = 0b1000 Checked = 1\n"
        ".EndC\n"
    )


def test_clone_and_rename_export_preserve_parts_and_isolate_bodies(tmp_path: Path) -> None:
    source = tmp_path / "source.spd"
    source.write_text(_board(), encoding="utf-8")
    inventory = scan_spd_inventory(source)
    c1, c2 = inventory.blocks
    clone = replace(c1, component_name="C3", clone_source_name="C1")
    clone2 = replace(c1, component_name="C4", clone_source_name="C1")
    output = tmp_path / "clone.spd"
    write_spd_with_replacements(
        source,
        output,
        [c1, c2, clone, clone2],
        {"C3": "edited clone\n", "C4": "second clone\n"},
        refdes_records=inventory.refdes_records,
        refdes_activation_status_changes={"X2": "Disabled"},
        component_renames={"C2": "C9"},
        component_clones={"C3": "C1", "C4": "C1"},
    )
    text = output.read_text(encoding="utf-8")
    assert ".Part C3 W=1\n+ description=source" in text
    assert ".Part C4 W=1\n+ description=source" in text
    assert text.count(".PartialCkt C3") == 1 and text.count(".PartialCkt C4") == 1
    assert ".Part C9 W=2" in text and ".PartialCkt C9 ExtNode" in text
    assert ".Connect X2 C9 Usage = 0b111000" in text
    assert ".PartialCkt C1 ExtNode =  1 2\nR 1 2 1" in text
    assert "edited clone\n.EndPartialCkt" in text

    renamed = tmp_path / "renamed.spd"
    write_spd_with_replacements(
        source, renamed, [c1, c2], {},
        component_renames={"C1": "C9"}, refdes_records=inventory.refdes_records,
    )
    renamed_text = renamed.read_text(encoding="utf-8")
    assert ".Part C9 W=1\n+ description=source" in renamed_text
    assert ".PartialCkt C9 ExtNode" in renamed_text
    assert ".Connect X1 C9 Usage = 0b1000" in renamed_text


def test_ui_clone_and_rename_reconcile_refdes(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.spd"
    source.write_text(_board(), encoding="utf-8")
    window = MainWindow()
    window.spd_path = source
    window.inventory = scan_spd_inventory(source)
    window.blocks = window.inventory.blocks
    window.refdes_records = window.inventory.refdes_records
    window.rebuild_refdes_groups()
    window.populate_components()
    window.component_list.setCurrentRow(0)
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "CLONE")
    window.clone_current_component()
    assert window.component_clones == {"CLONE": "C1"}
    assert not window.refdes_by_component.get("CLONE")
    assert "cloned" in window.component_list.currentItem().text()
    window.component_list.setCurrentRow(0)
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "RENAMED")
    window.rename_current_component()
    assert window.blocks[0].component_name == "RENAMED"
    assert "renamed" in window.component_list.item(0).text()
    assert window.effective_component_for_refdes("X1") == "RENAMED"
    assert not window.refdes_component_undo_stack
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "CLONE2")
    window.component_list.setCurrentRow(0)
    window.clone_current_component()
    assert window.component_clones["CLONE2"] == "C1"
    window.close()
    assert app is not None


def test_clone_writer_rejects_invalid_or_unknown_identities(tmp_path: Path) -> None:
    source = tmp_path / "source.spd"
    source.write_text(_board(), encoding="utf-8")
    inventory = scan_spd_inventory(source)
    output = tmp_path / "out.spd"
    with pytest.raises(ValueError):
        write_spd_with_replacements(source, output, inventory.blocks, {}, component_renames={"NOPE": "X"})
    with pytest.raises(ValueError, match="collision"):
        write_spd_with_replacements(source, output, inventory.blocks, {}, component_renames={"C1": "C2"})
    with pytest.raises(ValueError):
        write_spd_with_replacements(source, output, inventory.blocks, {}, component_clones={"BAD NAME": "C1"})
    no_part = tmp_path / "no_part.spd"
    no_part.write_text(".PartialCkt X ExtNode = 1\nbody\n.EndPartialCkt\n", encoding="utf-8")
    inv = scan_spd_inventory(no_part)
    with pytest.raises(ValueError, match=r"lacks a \.Part"):
        write_spd_with_replacements(no_part, output, inv.blocks, {}, component_clones={"Y": "X"})
