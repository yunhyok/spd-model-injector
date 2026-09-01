# SPD Model Injector

SPD Model Injector is a Windows desktop tool for injecting SPICE model text into Cadence PowerSI `.spd` files.

The app scans `.PartialCkt` / `.EndPartialCkt` blocks, lets you select a component, and replaces that component's existing PartialCkt body with a prepared SPICE model body. It is designed for large SPD files, so scanning and output writing use streaming file I/O instead of loading the entire board file into memory.

## Features

- Load large text-format PowerSI `.spd` files.
- List `.PartialCkt` components with detected `ExtNode` port counts.
- Show RefDes records for the selected component from `.Connect` lines.
- Display RefDes activation status as `Automatic`, `Enabled`, `Disabled`, or `Unknown`.
- Export all RefDes records to an Excel `.xlsx` file with component, RefDes name, and activation status.
- Paste or drag-and-drop `.mod` / `.txt` SPICE model text.
- Parse `.SUBCKT` headers, including `+` continuation lines.
- Map model ports to the selected PartialCkt `ExtNode` order.
- Preserve vendor comment lines before `.SUBCKT`.
- Remove `.SUBCKT` and `.ENDS` wrapper lines before injection.
- Replace the existing PartialCkt body and export to a new SPD path.
- Right-click a PartialCkt component to clone it (without cloning RefDes instances) or rename it; exports preserve `.Part` continuation lines and update `.Connect` references.
- Queue exact two-terminal PowerSI ports for selected RefDes rows, then write them only through `Export New SPD` without modifying the source file.
- Write output as UTF-8 with LF line endings.

## Generate Port

The menu bar is organized as `File`, `Edit`, `Model`, `Port`, `View`, and `Help`. After loading an SPD, the right edge provides separate `Model & RefDes` and `Port Generation` workspaces. Select one exact target NET, one exact reference NET, and one or more RefDes rows in the Port Generation workspace, then choose `Generate Port` to queue the requests. Review the queue and use `Export New SPD` to write them immediately before the existing `.EndPort`; the source file is never modified. Components with missing, extra, unannotated, or ambiguous Package Nodes are rejected as one batch.

Port Generation is unavailable when the loaded SPD does not contain one safe existing `.Port`/`.EndPort` section. The application does not create missing Port sections.

## Port Mapping Rule

Model ports are mapped by order:

```text
.PartialCkt C1 ExtNode =  1 2
.SUBCKT CAP Port1 Port2
```

`Port1` maps to `1`, and `Port2` maps to `2`.

For non-numeric SPD nodes:

```text
.PartialCkt U1 ExtNode =  LGA_A1 LGA_A2
+ LGA_A3 LGA_A4
.SUBCKT DEVICE P01 P02 P03 P04
```

`P01..P04` map to `LGA_A1..LGA_A4`. Export is blocked if the model port count and PartialCkt `ExtNode` count do not match.

## Development

Requirements:

- Python 3.12
- PySide6
- openpyxl
- pytest
- PyInstaller

Run tests:

```powershell
python -m pip install -e .
python -m pytest
```

Run the app:

```powershell
python -m spd_model_injector.app
```

Build the executable:

```powershell
.\scripts\build.ps1
```

The script builds with PyInstaller. If Inno Setup's `iscc` command is installed, it also creates a setup executable.

## Repository Safety

Large board files and vendor model files can contain sensitive design data, so `*.spd` and `*.mod` are ignored by default. Keep sample boards outside Git history and attach release artifacts through GitHub Releases instead.
