from __future__ import annotations

from pathlib import Path
from typing import Sequence

from openpyxl import Workbook

from spd_model_injector.core.spd import RefDesRecord


def export_refdes_xlsx(path: str | Path, records: Sequence[RefDesRecord]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RefDes"
    sheet.append(["Component", "RefDes Name", "Activation Status"])
    for record in records:
        sheet.append([record.component_name, record.refdes_name, record.activation_status])
    workbook.save(Path(path))
