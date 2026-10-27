import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spd_model_injector.core import model_library as library_core
from spd_model_injector.core.spd import PartialCktBlock
from spd_model_injector.ui import model_library as library_ui
from spd_model_injector.ui.main_window import MainWindow


def _spin_until(app, predicate, what, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(f"Timed out waiting for {what}")


@pytest.fixture
def windows(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(library_ui, "QSettings", lambda *args: settings)
    created = []

    def create(**kwargs):
        window = MainWindow(**kwargs)
        created.append(window)
        return window

    yield app, create, settings
    for window in created:
        window.model_library.cancel_indexing()
        _spin_until(app, lambda: not window.model_library.is_busy, "index thread cleanup")
        window.close()
        window.deleteLater()
    app.processEvents()


def _model(path, *, comment="Murata 0402 100nF", ports="P N", value="100n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"* {comment}\n.SUBCKT CAP {ports}\nC1 P N {value}\n.ENDS CAP\n", encoding="utf-8")
    return path


def _indexed(window, tmp_path):
    root = tmp_path / "models"
    source = _model(root / "Murata" / "part.mod")
    destination = tmp_path / "library.sqlite"
    library_core.build_index(root, destination)
    window.model_library.open_index(destination)
    _spin_until(QApplication.instance(), lambda: not window.model_library.is_busy, "opening test index")
    return root, source, destination


def _selected_component(window):
    window.blocks = [PartialCktBlock("BOARD_CAP", ["1", "2"], 1, 3, 0, 30, 40, 55, [])]
    window.populate_components()
    window.component_list.setCurrentRow(0)
    window.editor.setPlainText("Roriginal 1 2 1\n")
    return window.editor.toPlainText(), dict(window.replacements)


@pytest.fixture
def blocked_model_read(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = library_core.read_model_text

    def read(path):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release the model reader")
        return original(path)

    def block():
        monkeypatch.setattr(library_core, "read_model_text", read)
        return entered, release

    yield block
    release.set()


def test_model_library_menu_and_search_are_above_editor(windows):
    app, create, _ = windows
    window = create()
    window.show()
    app.processEvents()
    panel = window.model_library
    top_actions = window.menuBar().actions()
    model_action = next(action for action in top_actions if action.text() == "Model")
    model_menu = model_action.menu()
    model_actions = model_menu.actions()
    library_action = next(action for action in model_actions if action.text() == "Model Library")
    menu = library_action.menu()
    assert all(action in menu.actions() for action in (panel.create_action, panel.open_action, panel.save_action, panel.update_action))
    assert panel.search.mapTo(window, panel.search.rect().bottomLeft()).y() < window.editor.mapTo(window, window.editor.rect().topLeft()).y()
    assert panel.search.accessibleName()
    assert not panel.search.isEnabled()
    assert not panel.load_button.isEnabled()


def test_folder_save_dialog_indexes_nested_models_then_search_loads_selected_component(windows, tmp_path, monkeypatch):
    app, create, settings = windows
    window = create()
    _selected_component(window)
    panel = window.model_library
    root = tmp_path / "models"
    _model(root / "Murata" / "0402" / "GRM123.mod", comment="X7R 0.1uF low ESR")
    _model(root / "TDK" / "other.mod", comment="0603 10uF")
    destination = tmp_path / "chosen.sqlite"
    monkeypatch.setattr(library_ui.QFileDialog, "getExistingDirectory", lambda *args: str(root))
    monkeypatch.setattr(library_ui.QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))

    panel.create_action.trigger()
    _spin_until(app, lambda: not panel.is_busy, "folder index")

    assert destination.is_file()
    assert settings.value("modelLibrary/indexPath") == str(destination.resolve())
    for query in ("grm123", "murata 0402", "low ESR", "100 nF"):
        panel.search.setText(query)
        _spin_until(app, lambda: panel.results.topLevelItemCount() == 1, "debounced search")
        assert panel.results.topLevelItem(0).text(0) == "GRM123.mod"
    QTest.keyClick(panel.search, Qt.Key.Key_Return)
    assert "C1 1 2 100n" in window.editor.toPlainText()
    assert window.replacements["BOARD_CAP"] == window.editor.toPlainText()
    assert "OK" in window.validation_label.text()


def test_cancelled_save_dialog_does_not_start_indexing(windows, tmp_path, monkeypatch):
    _, create, settings = windows
    panel = create().model_library
    root = _model(tmp_path / "models" / "part.mod").parent
    monkeypatch.setattr(library_ui.QFileDialog, "getExistingDirectory", lambda *args: str(root))
    monkeypatch.setattr(library_ui.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    panel.create_index_dialog()
    assert not panel.is_busy
    assert panel.index is None
    assert not settings.contains("modelLibrary/indexPath")


def test_last_index_reopens_in_new_window_with_temporary_settings(windows, tmp_path):
    app, create, settings = windows
    first = create()
    _, _, destination = _indexed(first, tmp_path)
    settings.sync()
    restored = create().model_library
    _spin_until(app, lambda: not restored.is_busy, "restoring last index")
    assert restored.index_path == destination.resolve()
    assert restored.results.topLevelItemCount() == 1
    assert restored.search.isEnabled()


def test_smoke_window_can_suppress_saved_index_restore(windows, tmp_path, monkeypatch):
    _, create, settings = windows
    settings.setValue("modelLibrary/indexPath", str(tmp_path / "saved.sqlite"))

    def unexpected_load(*args, **kwargs):
        pytest.fail("Smoke window tried to restore a saved model index")

    monkeypatch.setattr(library_ui, "load_index", unexpected_load)
    panel = create(restore_model_library=False).model_library
    assert not panel.is_busy
    assert panel.index is None
    assert panel._thread is None


def test_corrupt_or_missing_open_preserves_active_index_and_saved_path(windows, tmp_path, monkeypatch):
    app, create, settings = windows
    panel = create().model_library
    root = _model(tmp_path / "models" / "part.mod").parent
    destination = tmp_path / "good.sqlite"
    library_core.build_index(root, destination)
    panel.open_index(destination)
    _spin_until(app, lambda: not panel.is_busy, "opening valid index")
    previous = panel.index
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a database")
    for candidate in (corrupt, tmp_path / "missing.sqlite"):
        monkeypatch.setattr(library_ui.QFileDialog, "getOpenFileName", lambda *args: (str(candidate), ""))
        panel.open_action.trigger()
        _spin_until(app, lambda: not panel.is_busy, "failed open")
        assert panel.index is previous
        assert panel.index_path == destination.resolve()
        assert settings.value("modelLibrary/indexPath") == str(destination.resolve())
        assert panel.results.topLevelItemCount() == 1
        assert "failed" in panel.status.text().lower()
    assert not (tmp_path / "missing.sqlite").exists()


def test_save_as_then_update_adds_changes_and_deletes_without_changing_old_index(windows, tmp_path, monkeypatch):
    app, create, settings = windows
    window = create()
    root, source, original = _indexed(window, tmp_path)
    original_bytes = original.read_bytes()
    copy = tmp_path / "copy.sqlite"
    panel = window.model_library
    monkeypatch.setattr(library_ui.QFileDialog, "getSaveFileName", lambda *args: (str(copy), ""))
    panel.save_action.trigger()
    _spin_until(app, lambda: not panel.is_busy, "saving index copy")
    assert panel.index_path == copy.resolve()
    assert settings.value("modelLibrary/indexPath") == str(copy.resolve())
    _model(source, comment="updated 1uF", value="1u")
    deleted = _model(root / "delete.mod")
    panel.update_action.trigger()
    _spin_until(app, lambda: not panel.is_busy, "first update")
    deleted.unlink()
    _model(root / "added.mod", comment="added 10uF")
    panel.update_action.trigger()
    _spin_until(app, lambda: not panel.is_busy, "second update")
    entries = library_core.load_index(copy).entries
    assert {entry.path.name for entry in entries} == {"part.mod", "added.mod"}
    assert next(entry.comments for entry in entries if entry.path == source) == "updated 1uF"
    assert original.read_bytes() == original_bytes


@pytest.mark.parametrize("failure", ["missing", "port_mismatch"])
def test_unreadable_or_invalid_model_does_not_overwrite_editor(windows, tmp_path, failure):
    _, create, _ = windows
    window = create()
    _, source, _ = _indexed(window, tmp_path)
    text, replacements = _selected_component(window)
    if failure == "missing":
        source.unlink()
    else:
        _model(source, ports="P N EXTRA")
    window.model_library.load_button.click()
    assert window.editor.toPlainText() == text
    assert window.replacements == replacements
    expected_error = "Could not load" if failure == "missing" else "port count"
    assert expected_error in window.validation_label.text()


def test_loading_without_component_is_rejected(windows, tmp_path):
    _, create, _ = windows
    window = create()
    _indexed(window, tmp_path)
    window.model_library.load_selected()
    assert window.editor.toPlainText() == ""
    assert window.replacements == {}
    assert "Select a PartialCkt" in window.validation_label.text()


def test_workspace_busy_disables_actions_and_blocks_direct_entrypoints(windows, tmp_path, monkeypatch):
    _, create, _ = windows
    window = create()
    root, _, destination = _indexed(window, tmp_path)
    panel = window.model_library
    text, replacements = _selected_component(window)
    before = destination.read_bytes()
    original_index = panel.index

    def unexpected_dialog(*args):
        pytest.fail("A busy workspace opened a library file dialog")

    for name in ("getExistingDirectory", "getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(library_ui.QFileDialog, name, unexpected_dialog)
    window._set_busy(True)
    assert all(not action.isEnabled() for action in (panel.create_action, panel.open_action, panel.save_action, panel.update_action))
    assert not panel.search.isEnabled()
    assert not panel.load_button.isEnabled()
    panel.create_index_dialog()
    panel.open_index_dialog()
    panel.save_index_dialog()
    panel.open_index(tmp_path / "missing.sqlite")
    panel.update_index()
    panel.start_indexing(root, tmp_path / "unexpected.sqlite")
    panel.load_selected()
    assert not panel.is_busy
    assert panel.index is original_index
    assert destination.read_bytes() == before
    assert not (tmp_path / "unexpected.sqlite").exists()
    assert window.editor.toPlainText() == text
    assert window.replacements == replacements
    window._set_busy(False)
    assert panel.load_button.isEnabled()


def test_failed_background_reindex_retains_previous_index(windows, tmp_path):
    app, create, settings = windows
    window = create()
    _, _, destination = _indexed(window, tmp_path)
    panel = window.model_library
    previous = panel.index
    before = destination.read_bytes()
    panel.start_indexing(tmp_path / "missing-folder", destination)
    _spin_until(app, lambda: not panel.is_busy, "failed index thread")
    assert panel.index is previous
    assert destination.read_bytes() == before
    assert settings.value("modelLibrary/indexPath") == str(destination.resolve())
    assert "failed" in panel.status.text().lower()
    assert panel.update_action.isEnabled()


def test_cancel_background_index_preserves_previous_file_and_finishes_thread(windows, tmp_path, blocked_model_read):
    app, create, _ = windows
    window = create()
    root, _, destination = _indexed(window, tmp_path)
    panel = window.model_library
    previous = panel.index
    before = destination.read_bytes()
    entered, release = blocked_model_read()
    try:
        panel.start_indexing(root, destination)
        _spin_until(app, entered.is_set, "active background read")
        finished = []
        panel._thread.finished.connect(lambda: finished.append(True))
        assert panel._thread.isRunning()
        assert panel.cancel_action.isEnabled()
        panel.cancel_action.trigger()
        assert panel._thread.isInterruptionRequested()
    finally:
        release.set()
    _spin_until(app, lambda: not panel.is_busy, "cancelled index thread")
    assert finished
    assert panel.index is previous
    assert destination.read_bytes() == before
    assert "cancelled" in panel.status.text().lower()
    assert not panel.cancel_action.isEnabled()
    assert not list(tmp_path.glob(".model-index-*.tmp"))


def test_close_during_index_cancels_and_waits_for_thread_before_accepting(windows, tmp_path, blocked_model_read):
    app, create, _ = windows
    entered, release = blocked_model_read()
    window = create()
    root = _model(tmp_path / "models" / "part.mod").parent
    panel = window.model_library
    panel.start_indexing(root, tmp_path / "library.sqlite")
    _spin_until(app, entered.is_set, "active index before close")
    event = QCloseEvent()
    try:
        window.closeEvent(event)
        assert not event.isAccepted()
        assert panel._thread.isInterruptionRequested()
        assert "Cancelling" in window.status_label.text()
    finally:
        release.set()
    _spin_until(app, lambda: not panel.is_busy, "thread completion before close")
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert not (tmp_path / "library.sqlite").exists()


@pytest.mark.parametrize("operation", ["open", "restore", "save_as"])
def test_open_restore_and_save_remain_responsive_and_cancel_safely(windows, tmp_path, monkeypatch, operation):
    app, create, settings = windows
    window = create()
    _, _, original_path = _indexed(window, tmp_path)
    panel = window.model_library
    previous = panel.index
    original_bytes = original_path.read_bytes()
    other_root = _model(tmp_path / "other-models" / "different.mod").parent
    destination = tmp_path / "other.sqlite"
    library_core.build_index(other_root, destination)
    destination_bytes = destination.read_bytes()
    entered, release = threading.Event(), threading.Event()
    function_name = "save_index_as" if operation == "save_as" else "load_index"
    original_operation = getattr(library_ui, function_name)

    def delayed_operation(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release library operation")
        return original_operation(*args, **kwargs)

    monkeypatch.setattr(library_ui, function_name, delayed_operation)
    try:
        if operation == "open":
            panel.open_index(destination)
        elif operation == "restore":
            panel = create().model_library
            previous = None
        else:
            monkeypatch.setattr(library_ui.QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
            panel.save_action.trigger()
        _spin_until(app, entered.is_set, "blocked library operation")
        assert panel.is_busy
        assert panel._thread.isRunning()
        assert not panel.create_action.isEnabled()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _spin_until(app, lambda: bool(ticks), "responsive UI timer")
        assert not release.is_set()
        panel.cancel_action.trigger()
        assert panel._thread.isInterruptionRequested()
    finally:
        release.set()
    _spin_until(app, lambda: not panel.is_busy, "cancelled library operation")
    assert panel.index is previous
    assert panel.index_path == (None if operation == "restore" else original_path.resolve())
    assert settings.value("modelLibrary/indexPath") == str(original_path.resolve())
    assert original_path.read_bytes() == original_bytes
    assert destination.read_bytes() == destination_bytes
    assert "cancelled" in panel.status.text().lower()
