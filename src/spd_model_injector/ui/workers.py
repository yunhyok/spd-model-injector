from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from spd_model_injector.core.spd import PartialCktBlock, scan_spd, write_spd_with_replacements


class ScanWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(scan_spd(self.path))
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
    ) -> None:
        super().__init__()
        self.source_path = Path(source_path)
        self.output_path = Path(output_path)
        self.blocks = blocks
        self.replacements = replacements

    @Slot()
    def run(self) -> None:
        try:
            write_spd_with_replacements(self.source_path, self.output_path, self.blocks, self.replacements)
            self.finished.emit(str(self.output_path))
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.failed.emit(str(exc))
