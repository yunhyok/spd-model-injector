import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from spd_model_injector.core.settings_transfer import CopySettingsOptions
from spd_model_injector.core.spd import scan_spd_inventory
from spd_model_injector.ui import settings_transfer
from spd_model_injector.ui.main_window import MainWindow
from spd_model_injector.ui.settings_transfer import SettingsCopyDialog


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    target = tmp_path / "target.spd"
    target.write_text(
        ".PartialCkt CAP_1 ExtNode = A B\nC1 A B 1u\n.EndPartialCkt\n"
        ".PartialCkt CAP_2 ExtNode = A B\nC1 A B 2u\n.EndPartialCkt\n",
        encoding="utf-8",
    )
    widget = MainWindow(restore_model_library=False)
    assert not widget.copy_settings_action.isEnabled()
    widget._pending_spd_path = target
    widget._scan_finished(scan_spd_inventory(target))
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def _wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


def _record_messages(monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: messages.append((box.icon(), box.text(), box.detailedText())))
    return messages


def test_menu_passes_loaded_target_and_keeps_the_workspace(window, tmp_path, monkeypatch):
    output = tmp_path / "settings.spd"
    target, inventory = window.spd_path, window.inventory

    def copy_dialog(dialog):
        assert dialog.target_path == target
        assert dialog.target_signature == inventory.source_signature
        assert not window.copy_settings_action.isEnabled()
        dialog.output_path = output
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsCopyDialog, "exec", copy_dialog)
    assert window.copy_settings_action.isEnabled()
    window._set_busy(True)
    assert not window.copy_settings_action.isEnabled()
    window._set_busy(False)
    window.copy_settings_action.trigger()
    assert window.copy_settings_action.isEnabled()
    assert window.spd_path == target and window.inventory is inventory
    assert str(output) in window.status_label.text()


@pytest.mark.parametrize(
    "pending_field",
    [
        "replacements", "refdes_component_changes", "refdes_activation_status_changes", "component_renames",
        "component_clones", "pending_port_requests", "port_deletions", "port_enabled_changes", "dc_settings",
        "vrm_sink_settings", "batch_edit",
    ],
)
def test_pending_edits_block_settings_copy(window, monkeypatch, pending_field):
    if pending_field == "batch_edit":
        window.component_list.item(1).setSelected(True)
        window.editor.setPlainText("C1 A B 9u")
        assert not window.replacements
    else:
        monkeypatch.setattr(window, pending_field, {"changed": True})
    monkeypatch.setattr(SettingsCopyDialog, "exec", lambda dialog: pytest.fail("Copy opened with pending edits"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.copy_settings_action.trigger()
    assert len(warnings) == 1
    assert "Export New SPD" in warnings[0]
    if pending_field == "batch_edit":
        assert "Validate/Convert" in warnings[0]


def test_dialog_copies_selected_settings_and_stays_open_while_working(window, tmp_path, monkeypatch):
    source, output = tmp_path / "source.spd", tmp_path / "new.spd"
    source.write_text("source", encoding="utf-8")
    output.write_bytes(b"previous output")
    before = window.spd_path.read_bytes()
    messages = _record_messages(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    save_paths = []

    def save_file(*args, **kwargs):
        save_paths.append(Path(args[2]))
        return str(output), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_file)
    release = threading.Event()
    calls = []

    def copy_settings(source_path, target_path, output_path, options, *, target_signature, progress_callback):
        calls.append((source_path, target_path, output_path, options, target_signature))
        progress_callback("Validating layers...", 2**31, 2**32)
        assert release.wait(5)
        output_path.write_bytes(b"new SPD")
        return ["Layer stackup copied.", "Ports copied."]

    monkeypatch.setattr(settings_transfer, "copy_spd_settings", copy_settings)
    dialog = SettingsCopyDialog(window.spd_path, window.inventory.source_signature, window)
    dialog.show()
    assert all(check.isChecked() for check in dialog.category_checks.values())
    assert not dialog.copy_button.isEnabled()
    dialog.browse_button.click()
    assert dialog.source_edit.text() == str(source)
    assert dialog.copy_button.isEnabled()
    for check in dialog.category_checks.values():
        check.setChecked(False)
    assert not dialog.copy_button.isEnabled()
    dialog.category_checks["stackup"].setChecked(True)
    dialog.category_checks["ports"].setChecked(True)
    dialog.copy_button.click()
    try:
        _wait_until(lambda: dialog.progress.value() == 50)
        assert dialog.status.text() == "Validating layers..."
        assert not dialog.cancel_button.isEnabled() and not dialog.copy_button.isEnabled()
        assert not dialog.close()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.isVisible()
    finally:
        release.set()
        _wait_until(lambda: dialog._worker is None)

    assert calls == [(
        source, window.spd_path, output, CopySettingsOptions(channel_activation=False, component_models=False),
        window.inventory.source_signature,
    )]
    assert save_paths == [window.spd_path.with_name("target_settings.spd")]
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.output_path == output and output.read_bytes() == b"new SPD"
    assert window.spd_path.read_bytes() == before and source.read_text(encoding="utf-8") == "source"
    assert len(messages) == 1
    icon, message, detail = messages[0]
    assert icon == QMessageBox.Icon.Information
    assert not detail
    assert all(text in message for text in ("Layer stackup copied.", "Ports copied.", str(output), "Load this SPD"))


@pytest.mark.parametrize("long_message", [False, True])
def test_validation_failure_warns_and_preserves_the_dialog(window, tmp_path, monkeypatch, long_message):
    output = tmp_path / "new.spd"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(output), ""))
    messages = _record_messages(monkeypatch)
    error = "NET names differ."
    if long_message:
        error += "\n" + "Long NET name " * 100

    def copy_settings(*args, **kwargs):
        raise ValueError(error)

    monkeypatch.setattr(settings_transfer, "copy_spd_settings", copy_settings)
    dialog = SettingsCopyDialog(window.spd_path, window.inventory.source_signature, window)
    dialog.show()
    dialog.source_edit.setText(str(tmp_path / "source.spd"))
    dialog.copy_button.click()
    _wait_until(lambda: dialog._worker is None)
    assert messages == [(
        QMessageBox.Icon.Warning, "NET names differ.", error if long_message else "",
    )]
    assert dialog.isVisible() and dialog.copy_button.isEnabled()
    assert dialog.output_path is None and not output.exists()
    dialog.close()


def test_cancelling_save_prevents_copy(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    messages = _record_messages(monkeypatch)
    dialog = SettingsCopyDialog(window.spd_path, window.inventory.source_signature, window)
    dialog.source_edit.setText(str(tmp_path / "source.spd"))
    dialog.copy_button.click()
    assert dialog._worker is None and dialog.output_path is None
    assert not messages
