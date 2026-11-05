import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from spd_model_injector.core.spd import scan_spd_inventory
from spd_model_injector.core.spice import prepare_model_for_partialckt
from spd_model_injector.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "board.spd"
    source.write_text(
        ".PartialCkt CAP ExtNode = A B\n* original\nC1 A B 1u\n.EndPartialCkt\n"
        ".PartialCkt EMPTY ExtNode = 1 2\n* no model\n.EndPartialCkt\n",
        encoding="utf-8",
    )
    widget = MainWindow(restore_model_library=False)
    assert not widget.export_model_action.isEnabled()
    assert not widget.export_model_button.isEnabled()
    widget._pending_spd_path = source
    widget._scan_finished(scan_spd_inventory(source))
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_export_dialog_saves_original_and_reimports_without_changing_workspace(window, tmp_path, monkeypatch):
    source_before = window.spd_path.read_bytes()
    output = tmp_path / "exported.mod"
    dialogs = []

    def save_dialog(*args):
        dialogs.append(args)
        return str(output), "SPICE models (*.mod)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    window.editor.setPlainText("Cedited A B 9u\n")
    staged = dict(window.replacements)
    window.export_model_action.trigger()
    assert Path(dialogs[0][2]).name == "model_CAP.mod"
    text = output.read_text(encoding="utf-8")
    assert ".SUBCKT CAP A B\n" in text
    assert "C1 A B 1u\n" in text and "Cedited" not in text
    assert "C1 X Y 1u" in prepare_model_for_partialckt(text, ["X", "Y"])
    assert window.replacements == staged
    assert window.spd_path.read_bytes() == source_before
    assert "Exported original SPICE model CAP" in window.status_label.text()


def test_export_uses_original_name_after_rename_and_blocks_new_clones(window, tmp_path, monkeypatch):
    output = tmp_path / "original.mod"
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "RENAMED")
    window.rename_current_component()
    assert window.export_model_action.isEnabled()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window.export_model_button.click()
    assert ".SUBCKT CAP A B" in output.read_text(encoding="utf-8")
    monkeypatch.setattr(window, "_prompt_component_name", lambda *args: "CLONE")
    window.clone_current_component()
    assert not window.export_model_action.isEnabled()
    assert not window.export_model_button.isEnabled()


def test_export_requires_one_visible_selection_and_idle_workspace(window, monkeypatch):
    assert window.export_model_action.isEnabled()
    window.component_list.item(1).setSelected(True)
    assert not window.export_model_action.isEnabled()
    window.component_list.item(1).setSelected(False)
    assert window.export_model_action.isEnabled()
    window._set_busy(True)
    assert not window.export_model_action.isEnabled()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: pytest.fail("busy export opened dialog"))
    window.export_model_dialog()
    window._set_busy(False)
    assert window.export_model_action.isEnabled()
    window.component_filter.setText("missing")
    assert not window.export_model_action.isEnabled()
    assert not window.export_model_button.isEnabled()


def test_cancel_export_does_not_write_or_report_success(window, monkeypatch):
    status = window.status_label.text()
    files = sorted(window.spd_path.parent.iterdir())
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    window.export_model_dialog()
    assert window.status_label.text() == status
    assert sorted(window.spd_path.parent.iterdir()) == files


@pytest.mark.parametrize("failure", ["empty", "stale", "source"])
def test_export_error_is_reported_and_existing_files_are_preserved(window, tmp_path, monkeypatch, failure):
    output = tmp_path / "previous.mod"
    output.write_bytes(b"previous successful model")
    if failure == "empty":
        window.component_list.setCurrentRow(1)
    elif failure == "stale":
        window.spd_path.write_bytes(b"new header\n" + window.spd_path.read_bytes())
    else:
        output = window.spd_path
    before = output.read_bytes()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window.export_model_dialog()
    assert len(warnings) == 1
    assert "SPICE export failed:" in window.status_label.text()
    assert output.read_bytes() == before
