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
- Select multiple Power channels, queue PowerSI ports for Component→RefDes tree rows, and merge every matching Package.Node pin into each terminal.
- Inspect existing and pending Ports, change activation with checkboxes, and queue Port deletion or restoration before export.
- Queue PowerDC DC settings (`Voltage` and pairing `GroundNet`) per Power NET channel, with search, multi-selection, and voltage auto-fill from the NET name.
- Write output as UTF-8 with LF line endings.

## Generate Port

The menu bar is organized as `File`, `Edit`, `Model`, `Port`, `DC`, `View`, and `Help`. The top tabs provide separate `Model & RefDes`, `Port Generation`, and `DC Setting` workspaces. The Port workspace is split left/right: select one or more Power channels on the left, expand the Component→RefDes tree, select the required RefDes rows, and choose `Generate Port`. `DGND` is used automatically when present; only files without `DGND` prompt for an exact reference NET. The right side lists existing and pending Ports with pin counts, activation checkboxes, and deletion/restore controls. `Export New SPD` applies the queued changes without modifying the source file.

In 0.6.0, `Ctrl+F` focuses the component search in `Model & RefDes`, or the Power NET search in `Port Generation` (and, since 0.7.0, the channel search in `DC Setting`). Power NET search is case-insensitive and filters the displayed list without changing checked targets. The summary shows how many NETs are visible and how many are checked, including hidden items. Clear the search to see all NETs again; loading a new SPD resets the search. The separate RefDes instance search remains available below it.

Files without Ports are supported: export creates a `.Port`/`.EndPort` section at the reserved Port location, or before the valid `.NetList` section if no reserved location exists. Malformed or multiple Port sections remain blocked. Select the SITE0/SITE1 child rows under DUT after checking their Power NETs.

## DC Setting (0.7.0)

The `DC Setting` tab prepares a PowerSI board for PowerDC. It lists every `PowerNets` member of `.NetList` (active and inactive) with its current `Voltage` and `GroundNet`. Search filters the list, and rows support multi-selection (`Ctrl`/`Shift`-click, or `Ctrl+A` on the filtered list). Enter a `Pairing P/G NET` (defaults to `DGND` when present) and `Volt (V)`, then `Apply to Selected` to queue the setting for every selected visible row; `Auto-fill Selected` reads the voltage from each NET name instead (`VDD085` → 0.85 V, `VDD075` → 0.75 V, `VDD18` → 1.8 V, `VDD12` → 1.2 V, `1V8`/`0P75`/`1.2V` styles; 1 V when nothing looks like a voltage). Pending rows are highlighted and can be reverted or cleared before export.

On export each queued NET line gains `Voltage = <V> GroundNet = <NET>`, replacing any previous values, exactly as PowerDC writes them:

```text
	ADC_VDDI_TRIP0/0 Color = BLUE Voltage = 0.95 GroundNet = DGND
```

Applying a setting to an inactive (`::Unselected`) NET also activates it, because PowerDC only simulates active nets. The pairing NET must be an active NET in `.NetList`. Nothing else in the file is touched; the NetList is neither reordered nor re-flagged. Files saved by PowerDC (`||DropShape` flags on NET and group tokens) are now parsed correctly, so their Power NETs and `DGND` are available in `Port Generation` and `DC Setting`.

## Export and workspace safety (0.5.0)

- Repeated exports retain all staged model, RefDes, Port, and DC changes against the original source. Load a new SPD to start a new workspace.
- A failed load preserves the current workspace. Editing and closing are blocked while a scan or export is running.
- Export checks that the source file still matches its scanned file identity, size, and modification time. Reload the file if it was changed outside the app.
- Output is written to a temporary file in the destination folder and replaces the destination only after a successful write. Failed writes preserve an existing output file.
- Missing or nested `.PartialCkt` end markers are rejected. Full `.SUBCKT` text pasted into the editor is converted before export.
- SPICE conversion changes supported node fields only, preserving element names and values. Missing/mismatched `.ENDS`, node collisions, parameterized `.SUBCKT` headers, and unsupported or ambiguous element syntax are rejected instead of guessing. Complex vendor dialects may need a prepared PartialCkt body.

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
