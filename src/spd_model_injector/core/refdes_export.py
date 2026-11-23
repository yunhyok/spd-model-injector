from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence

from openpyxl import Workbook

from spd_model_injector.core.spd import RefDesRecord


def export_refdes_xlsx(path: str | Path, records: Sequence[RefDesRecord]) -> None:
    output = Path(path)
    workbook = Workbook()
    temporary_path: Path | None = None
    try:
        sheet = workbook.active
        sheet.title = "RefDes"
        sheet.append(["Component", "RefDes Name", "Activation Status", "Net Name"])
        for record in records:
            sheet.append([record.component_name, record.refdes_name, record.activation_status, record.net_name])
        with NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp.xlsx",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        workbook.save(temporary_path)
        os.replace(temporary_path, output)
    finally:
        workbook.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
