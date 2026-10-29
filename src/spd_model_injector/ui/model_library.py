from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from spd_model_injector.core.model_library import (
    LibraryIndex, LibraryIndexCancelled, build_index, load_index, read_model_text,
    save_index_as, search_entries,
)
from spd_model_injector.ui.theme import section_title


INDEX_FILTER = "SPICE model index (*.sqlite);;All files (*)"


class IndexThread(QThread):
    indexed = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, root: Path | None, destination: Path, parent: QWidget, *, source: Path | None = None) -> None:
        super().__init__(parent)
        self.root = root
        self.destination = destination
        self.source = source

    def run(self) -> None:
        try:
            if self.root is not None:
                index = build_index(
                    self.root, self.destination,
                    cancelled=self.isInterruptionRequested, progress=self.progress.emit,
                )
            elif self.source is not None:
                index = save_index_as(self.source, self.destination, cancelled=self.isInterruptionRequested)
            else:
                index = load_index(self.destination, cancelled=self.isInterruptionRequested)
            self.indexed.emit(index)
        except LibraryIndexCancelled:
            self.failed.emit("Model library operation cancelled. The previous index is unchanged.")
        except Exception as exc:
            self.failed.emit(f"Model library operation failed: {exc}")


class ModelLibrary(QWidget):
    modelSelected = Signal(str)
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, settings: QSettings | None = None, restore_last: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("model_library")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.settings = settings if settings is not None else QSettings("SPD Model Injector", "SPD Model Injector")
        self.index: LibraryIndex | None = None
        self.index_path: Path | None = None
        self._thread: IndexThread | None = None
        self._workspace_busy = False
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search models: part number, manufacturer, 0402, 100nF...")
        self.search.setToolTip("Search file/folder names, subcircuit names and model comments. All terms must match.")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search SPICE model library")
        self.load_button = QPushButton("Load Selected")
        self.load_button.clicked.connect(self.load_selected)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Model", "Folder", "Comments"])
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setColumnWidth(0, 180)
        self.results.setColumnWidth(1, 140)
        self.results.setMinimumHeight(76)
        self.results.itemActivated.connect(self.load_selected)
        self.results.itemSelectionChanged.connect(self._update_controls)
        self.results.hide()
        self.status = QLabel("Model library: use Model > Model Library > Index Model Folder...")
        self.status.setProperty("muted", True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(4)
        layout.addWidget(section_title("MODEL LIBRARY"))
        row = QHBoxLayout()
        row.addWidget(self.search, 1)
        row.addWidget(self.load_button)
        layout.addLayout(row)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.status)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self.refresh_results)
        self.search.textChanged.connect(self._queue_search)
        self.search.returnPressed.connect(self._search_and_load)
        self.create_action = QAction("Index Model Folder...", self)
        self.create_action.triggered.connect(self.create_index_dialog)
        self.open_action = QAction("Open Model Index...", self)
        self.open_action.triggered.connect(self.open_index_dialog)
        self.save_action = QAction("Save Model Index As...", self)
        self.save_action.triggered.connect(self.save_index_dialog)
        self.update_action = QAction("Update Model Index", self)
        self.update_action.triggered.connect(self.update_index)
        self.cancel_action = QAction("Cancel Library Operation", self)
        self.cancel_action.triggered.connect(self.cancel_indexing)
        last_path = self.settings.value("modelLibrary/indexPath", "", type=str)
        if restore_last and last_path:
            self.open_index(Path(last_path))
        self._update_controls()

    def add_menu(self, parent_menu: QMenu) -> None:
        menu = parent_menu.addMenu("Model Library")
        menu.addActions([self.create_action, self.open_action, self.save_action, self.update_action])
        menu.addSeparator()
        menu.addAction(self.cancel_action)

    @property
    def is_busy(self) -> bool:
        return self._thread is not None

    def set_workspace_busy(self, busy: bool) -> None:
        self._workspace_busy = busy
        self._update_controls()

    def _update_controls(self) -> None:
        available = not self._workspace_busy and not self.is_busy
        self.create_action.setEnabled(available)
        self.open_action.setEnabled(available)
        self.save_action.setEnabled(available and self.index is not None)
        self.update_action.setEnabled(available and self.index is not None)
        self.cancel_action.setEnabled(self.is_busy)
        self.search.setEnabled(available and self.index is not None)
        self.results.setEnabled(available)
        self.load_button.setEnabled(available and self.results.currentItem() is not None)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setToolTip(text)

    def _report_error(self, message: str) -> None:
        self._set_status(message)
        self.error.emit(message)

    def _default_save_path(self) -> str:
        if self.index_path:
            return str(self.index_path)
        directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
        return str(directory / "spice-model-index.sqlite")

    def create_index_dialog(self) -> None:
        if self.is_busy or self._workspace_busy:
            return
        root = QFileDialog.getExistingDirectory(self, "Choose SPICE model library folder", str(self.index.root) if self.index else "")
        if not root:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Save model index", self._default_save_path(), INDEX_FILTER)
        if destination:
            self.start_indexing(Path(root), Path(destination))

    def start_indexing(self, root: Path, destination: Path) -> None:
        if self.is_busy or self._workspace_busy:
            return
        self._start_job(IndexThread(root, destination, self), "Indexing model folder...")

    def _start_job(self, thread: IndexThread, message: str) -> None:
        self._search_timer.stop()
        self._thread = thread
        self._thread.indexed.connect(self._index_ready)
        self._thread.failed.connect(self._report_error)
        self._thread.progress.connect(lambda count: self._set_status(f"Indexing model folder... {count} files checked"))
        self._thread.finished.connect(self._index_finished)
        self._set_status(message)
        self._update_controls()
        self._thread.start()

    def cancel_indexing(self) -> None:
        if self._thread:
            self._thread.requestInterruption()
            self._set_status("Cancelling model library operation...")

    def _index_ready(self, index: LibraryIndex) -> None:
        self._use_index(index, self._thread.destination)

    def _index_finished(self) -> None:
        thread = self._thread
        self._thread = None
        thread.deleteLater()
        self._update_controls()

    def _use_index(self, index: LibraryIndex, path: Path) -> None:
        self.index = index
        self.index_path = path.resolve()
        self.settings.setValue("modelLibrary/indexPath", str(self.index_path))
        self.refresh_results()
        self._update_controls()

    def open_index_dialog(self) -> None:
        if self.is_busy or self._workspace_busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open model index", self._default_save_path(), INDEX_FILTER)
        if path:
            self.open_index(Path(path))

    def open_index(self, path: Path) -> None:
        if self.is_busy or self._workspace_busy:
            return
        self._start_job(IndexThread(None, path, self), "Loading model index...")

    def save_index_dialog(self) -> None:
        if self.is_busy or self._workspace_busy or self.index_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save model index as", self._default_save_path(), INDEX_FILTER)
        if not path:
            return
        self._start_job(IndexThread(None, Path(path), self, source=self.index_path), "Saving model index...")

    def update_index(self) -> None:
        if self.index is None or self.index_path is None or self.is_busy or self._workspace_busy:
            return
        root = self.index.root
        if not root.is_dir():
            folder = QFileDialog.getExistingDirectory(self, "Model folder moved: choose its new location")
            if not folder:
                return
            root = Path(folder)
        self.start_indexing(root, self.index_path)

    def _queue_search(self) -> None:
        self.results.clear()
        self._update_controls()
        self._search_timer.start()

    def refresh_results(self) -> None:
        self._search_timer.stop()
        self.results.clear()
        if self.index is None:
            return
        matches = search_entries(self.index.entries, self.search.text())
        for entry in matches[:200]:
            item = QTreeWidgetItem([
                entry.path.name, str(Path(entry.relative_path).parent), " ".join(entry.comments.split())[:180],
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(0, f"{entry.path}\n.SUBCKT {entry.subcircuit}")
            item.setToolTip(1, entry.relative_path)
            item.setToolTip(2, entry.comments)
            self.results.addTopLevelItem(item)
        self.results.setVisible(bool(self.index.entries))
        if self.results.topLevelItemCount():
            self.results.setCurrentItem(self.results.topLevelItem(0))
        count = f"{len(matches)} / {len(self.index.entries)} models"
        if len(matches) > 200:
            count += " (first 200 shown; refine search)"
        if self.index.skipped:
            count += f"; {self.index.skipped} files skipped"
        self._set_status(f"{count} | {self.index_path} | Folder: {self.index.root}")
        if self.index.warnings:
            self.status.setToolTip(self.status.text() + "\n" + "\n".join(self.index.warnings))
        self._update_controls()

    def _search_and_load(self) -> None:
        self.refresh_results()
        self.load_selected()

    def load_selected(self, *_args) -> None:
        if self.is_busy or self._workspace_busy:
            return
        item = self.results.currentItem()
        if item is None:
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            # Re-read the source so stale index contents never become injected model text.
            text = read_model_text(entry.path)
        except (OSError, ValueError) as exc:
            self._report_error(f"Could not load {entry.path.name}: {exc}. Update the model index after moving files.")
            return
        self.modelSelected.emit(text)
