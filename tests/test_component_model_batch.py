import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView

from spd_model_injector.core import model_library
from spd_model_injector.core.spd import read_block_body, scan_spd_inventory
from spd_model_injector.ui.main_window import MainWindow


MODEL = ".SUBCKT CAP P N\nC1 P N 1u\n.ENDS CAP\n"


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        QApplication.instance().processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for background work")


@pytest.fixture
def make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    windows = []

    def create(nodes=(('1', '2'), ('A', 'B'), ('X', 'Y'))):
        source = tmp_path / f"board{len(windows)}.spd"
        source.write_text(
            "".join(
                f".PartialCkt CAP_{index} ExtNode = {' '.join(ports)}\n"
                f"Roriginal {' '.join(ports[:2])} 1\n.EndPartialCkt\n"
                for index, ports in enumerate(nodes)
            ),
            encoding="utf-8",
        )
        window = MainWindow(restore_model_library=False)
        window.spd_path = source
        window.inventory = scan_spd_inventory(source)
        window.blocks = window.inventory.blocks
        window.populate_components()
        window.show()
        app.processEvents()
        windows.append(window)
        return window

    yield create
    for window in windows:
        wait_until(lambda: not window.model_library.is_busy and window._export_thread is None)
        window.close()
        window.deleteLater()
    app.processEvents()


def click(window, row, modifiers=Qt.KeyboardModifier.NoModifier):
    item = window.component_list.item(row)
    QTest.mouseClick(
        window.component_list.viewport(), Qt.MouseButton.LeftButton, modifiers,
        window.component_list.visualItemRect(item).center(),
    )
    QApplication.instance().processEvents()


def test_ctrl_and_shift_click_select_targets_without_applying_models(make_window):
    window = make_window()
    assert window.component_list.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    click(window, 0)
    click(window, 2, Qt.KeyboardModifier.ControlModifier)
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_0", "CAP_2"]
    assert "2 selected" in window.component_label.text()
    assert "CAP_2" in window.component_label.text()
    assert window.revert_button.text() == "Revert Selected"
    assert "Validate/Convert" in window.validation_label.text()
    click(window, 0)
    click(window, 2, Qt.KeyboardModifier.ShiftModifier)
    assert len(window.selected_component_blocks()) == 3
    assert "3 selected" in window.component_label.text()
    assert window.replacements == {}


@pytest.mark.parametrize("entry", ["direct", "import_file", "drop_file", "library"])
def test_shared_import_paths_map_each_selected_target(make_window, tmp_path, monkeypatch, entry):
    window = make_window()
    source_before = window.spd_path.read_bytes()
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    model_path = tmp_path / "models" / "cap.mod"
    model_path.parent.mkdir()
    model_path.write_text(MODEL, encoding="utf-8")
    if entry == "direct":
        window.import_model_text(MODEL)
    elif entry == "import_file":
        monkeypatch.setattr(
            "spd_model_injector.ui.main_window.QFileDialog.getOpenFileName",
            lambda *args: (str(model_path), ""),
        )
        window.import_model_dialog()
    elif entry == "drop_file":
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(model_path))])
        event = QDropEvent(
            QPointF(5, 5), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        window.editor.dropEvent(event)
        assert event.isAccepted()
    else:
        index = tmp_path / "models.sqlite"
        model_library.build_index(model_path.parent, index)
        window.model_library.open_index(index)
        wait_until(lambda: not window.model_library.is_busy)
        window.model_library.load_selected()
    assert window.replacements == {"CAP_0": "C1 1 2 1u\n", "CAP_1": "C1 A B 1u\n"}
    assert window.editor.toPlainText() == "C1 A B 1u\n"
    assert all(window.component_list.item(row).font().bold() for row in (0, 1))
    assert not window.component_list.item(2).font().bold()
    assert window.spd_path.read_bytes() == source_before


def test_batch_export_roundtrip_preserves_source_and_unselected_body(make_window, tmp_path):
    window = make_window()
    original = window.spd_path.read_bytes()
    unselected_body = read_block_body(window.spd_path, window.blocks[2])
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.import_model_text(MODEL)
    output = tmp_path / "injected.spd"
    window.export_spd(output)
    wait_until(lambda: not window._busy and window._export_thread is None)
    inventory = scan_spd_inventory(output)
    assert [read_block_body(output, block) for block in inventory.blocks] == [
        "C1 1 2 1u\n", "C1 A B 1u\n", unselected_body,
    ]
    assert window.spd_path.read_bytes() == original


def test_multiselection_with_populated_library_keeps_editor_visible_at_small_size(make_window, tmp_path):
    window = make_window()
    models = tmp_path / "models"
    models.mkdir()
    (models / "cap.mod").write_text(MODEL, encoding="utf-8")
    index = tmp_path / "models.sqlite"
    model_library.build_index(models, index)
    window.model_library.open_index(index)
    wait_until(lambda: not window.model_library.is_busy)
    window.resize(900, 600)
    QApplication.instance().processEvents()
    click(window, 0)
    click(window, 2, Qt.KeyboardModifier.ShiftModifier)
    assert window.size().width() == 900
    assert window.size().height() == 600
    assert window.editor.height() >= 80
    assert window.validate_button.isVisible()
    assert window.model_editor_panel.rect().contains(window.validate_button.geometry())
    assert window.model_editor_panel.rect().contains(window.revert_button.geometry())
    assert "3 selected" in window.component_label.text()


def test_pasted_full_model_waits_for_validate_then_maps_each_target(make_window):
    window = make_window()
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.editor.setPlainText(MODEL)
    assert window.replacements == {}
    assert "pending" in window.validation_label.text()
    window.validate_button.click()
    expected = {"CAP_0": "C1 1 2 1u\n", "CAP_1": "C1 A B 1u\n"}
    assert window.replacements == expected
    click(window, 2, Qt.KeyboardModifier.ControlModifier)
    assert window.replacements == expected
    assert "Roriginal" in window.editor.toPlainText()


@pytest.mark.parametrize("paste", [False, True])
@pytest.mark.parametrize(
    ("nodes", "model", "error"),
    [
        ((("1", "2"), ("A", "B", "C")), MODEL, "port count"),
        ((("1", "2"), ("inside", "B")), ".SUBCKT CAP P N\nR1 P inside 1\nC1 inside N 1u\n.ENDS CAP\n", "collides"),
        ((("1", "2"), ("A", "B")), ".SUBCKT CAP P N\nD1 P N diode\n.ENDS CAP\n", "Unsupported"),
    ],
)
def test_invalid_batch_keeps_all_staged_bodies_and_editor(make_window, paste, nodes, model, error):
    window = make_window(nodes)
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.replacements.update({"CAP_0": "old first\n", "CAP_1": "old second\n", "OTHER": "keep\n"})
    if paste:
        window.editor.setPlainText(model)
    before_text = window.editor.toPlainText()
    before_replacements = dict(window.replacements)
    if paste:
        window.validate_current_text()
    else:
        window.import_model_text(model)
    assert error in window.validation_label.text()
    assert window.replacements == before_replacements
    assert window.editor.toPlainText() == before_text


@pytest.mark.parametrize("other_nodes", [("A", "B"), ("2", "1")])
def test_raw_body_batch_requires_identical_ordered_ext_nodes(make_window, other_nodes):
    window = make_window((("1", "2"), other_nodes))
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.replacements["CAP_1"] = "keep previous\n"
    window.editor.setPlainText("Cmanual 1 2 2u")
    assert window.replacements == {"CAP_1": "keep previous\n"}
    window.validate_current_text()
    assert window.replacements == {"CAP_1": "keep previous\n"}
    assert "ordered ExtNode" in window.validation_label.text()
    assert "full .SUBCKT" in window.validation_label.text()


def test_raw_body_batch_with_identical_ext_nodes_applies_only_on_validate(make_window):
    window = make_window((("1", "2"), ("1", "2")))
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    window.editor.setPlainText("Cmanual 1 2 2u")
    assert window.replacements == {}
    window.validate_current_text()
    assert window.replacements == {"CAP_0": "Cmanual 1 2 2u\n", "CAP_1": "Cmanual 1 2 2u\n"}


def test_focus_on_unselected_row_keeps_assignments_scoped_to_selection(make_window):
    window = make_window()
    click(window, 0)
    window.component_list.setCurrentRow(2, QItemSelectionModel.SelectionFlag.NoUpdate)
    assert window.component_list.currentRow() == 2
    assert window.current_block().component_name == "CAP_0"
    assert "CAP_0" in window.component_label.text()
    window.import_model_text(MODEL)
    assert window.replacements == {"CAP_0": "C1 1 2 1u\n"}
    window.editor.setPlainText("Cmanual 1 2 2u\n")
    assert window.replacements == {"CAP_0": "Cmanual 1 2 2u\n"}
    assert window.component_list.item(0).font().bold()
    assert not window.component_list.item(2).font().bold()


def test_filter_excludes_hidden_targets_and_does_not_restore_their_selection(make_window):
    window = make_window()
    click(window, 0)
    click(window, 2, Qt.KeyboardModifier.ShiftModifier)
    window.component_filter.setText("CAP_1")
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_1"]
    assert "1 selected" in window.component_label.text()
    window.import_model_text(MODEL)
    assert window.replacements == {"CAP_1": "C1 A B 1u\n"}
    window.component_filter.clear()
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_1"]
    window.component_filter.setText("missing")
    assert not window.selected_component_blocks()
    before = dict(window.replacements)
    window.import_model_text(MODEL)
    window.editor.setPlainText("do not stage")
    window.validate_current_text()
    window.revert_current()
    assert window.replacements == before


def test_cleared_selection_and_hidden_current_cannot_mutate_stale_target(make_window):
    window = make_window()
    click(window, 0)
    window.component_list.clearSelection()
    assert window.component_list.currentRow() == 0
    window.editor.setPlainText(MODEL)
    window.validate_current_text()
    window.import_model_text(MODEL)
    assert window.replacements == {}
    click(window, 0)
    window.component_list.item(0).setHidden(True)
    window.import_model_text(MODEL)
    assert window.replacements == {}


def test_revert_selected_and_busy_guards_preserve_other_staged_targets(make_window):
    window = make_window()
    click(window, 0)
    click(window, 2, Qt.KeyboardModifier.ShiftModifier)
    window.import_model_text(MODEL)
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    previous = dict(window.replacements)
    window._set_busy(True)
    window.import_model_text(MODEL)
    window.editor.setPlainText("do not apply while busy")
    window.validate_current_text()
    window.revert_current()
    assert window.replacements == previous
    window._set_busy(False)
    window.revert_current()
    assert window.replacements == {"CAP_2": "C1 X Y 1u\n"}
    assert "Roriginal" in window.editor.toPlainText()
    assert not window.component_list.item(0).font().bold()
    assert not window.component_list.item(1).font().bold()
    assert window.component_list.item(2).font().bold()


def test_single_selection_manual_edit_and_full_model_conversion_are_retained(make_window):
    window = make_window()
    click(window, 0)
    window.editor.setPlainText("Cmanual 1 2 2u")
    assert window.replacements == {"CAP_0": "Cmanual 1 2 2u\n"}
    window.editor.setPlainText(MODEL)
    window.validate_current_text()
    assert window.replacements == {"CAP_0": "C1 1 2 1u\n"}
    assert window.editor.toPlainText() == "C1 1 2 1u\n"
    assert window.revert_button.text() == "Revert"


def test_clone_becomes_the_only_target_for_subsequent_model_import(make_window, monkeypatch):
    window = make_window()
    click(window, 0)
    window.editor.setPlainText("Rsource 1 2 7\n")
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "CAP_CLONE")
    window.clone_current_component()
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_CLONE"]
    window.import_model_text(MODEL)
    assert window.replacements == {"CAP_0": "Rsource 1 2 7\n", "CAP_CLONE": "C1 1 2 1u\n"}


def test_context_menu_preserves_selected_group_but_selects_an_unselected_target(make_window):
    window = make_window()
    click(window, 0)
    click(window, 1, Qt.KeyboardModifier.ControlModifier)
    QTimer.singleShot(0, lambda: QApplication.activePopupWidget().close())
    window._show_component_context_menu(window.component_list.visualItemRect(window.component_list.item(0)).center())
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_0", "CAP_1"]
    assert window.current_block().component_name == "CAP_0"
    QTimer.singleShot(0, lambda: QApplication.activePopupWidget().close())
    window._show_component_context_menu(window.component_list.visualItemRect(window.component_list.item(2)).center())
    assert [block.component_name for block in window.selected_component_blocks()] == ["CAP_2"]
    assert window.replacements == {}
