from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from spd_model_injector.core.spd import PartialCktBlock, RefDesRecord, scan_spd_inventory, write_spd_with_replacements


class ScanWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(scan_spd_inventory(self.path, progress_callback=self.progress.emit))
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.failed.emit(str(exc))


class ExportWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        source_path: str | Path,
        output_path: str | Path,
        blocks: list[PartialCktBlock],
        replacements: dict[str, str],
        refdes_component_changes: dict[str, str] | None = None,
        refdes_activation_status_changes: dict[str, str] | None = None,
        refdes_records: list[RefDesRecord] | None = None,
    ) -> None:
        super().__init__()
        self.source_path = Path(source_path)
        self.output_path = Path(output_path)
        self.blocks = blocks
        self.replacements = replacements
        self.refdes_component_changes = refdes_component_changes or {}
        self.refdes_activation_status_changes = refdes_activation_status_changes or {}
        self.refdes_records = refdes_records

    @Slot()
    def run(self) -> None:
        try:
            write_spd_with_replacements(
                self.source_path,
                self.output_path,
                self.blocks,
                self.replacements,
                refdes_component_changes=self.refdes_component_changes,
                refdes_activation_status_changes=self.refdes_activation_status_changes,
                refdes_records=self.refdes_records,
            )
            self.finished.emit(str(self.output_path))
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.failed.emit(str(exc))
