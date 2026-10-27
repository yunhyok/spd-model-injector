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
- Index a local model folder and search file/folder names, subcircuit names, and comments directly above the model editor.
- Parse `.SUBCKT` headers, including `+` continuation lines.
- Map model ports to the selected PartialCkt `ExtNode` order.
- Preserve vendor comment lines before `.SUBCKT`.
- Remove `.SUBCKT` and `.ENDS` wrapper lines before injection.
- Replace the existing PartialCkt body and export to a new SPD path.
- Right-click a PartialCkt component to clone it (without cloning RefDes instances) or rename it; exports preserve `.Part` continuation lines and update `.Connect` references.
- Select multiple Power channels, queue PowerSI ports for Component→RefDes tree rows, and merge every matching Package.Node pin into each terminal.
- Inspect existing and pending Ports, change activation with checkboxes, and queue Port deletion or restoration before export.
- Queue PowerDC DC settings (`Voltage` and pairing `GroundNet`) per Power NET channel, with search, multi-selection, and voltage auto-fill from the NET name.
- Change the properties of VRMs and Sinks already defined in the SPD (`NominalVoltage`, `SenseVoltage`, `OutputCurrent`, `Current`, `Model`, ...) for many rows at once, with search and multi-selection.
- Write output as UTF-8 with LF line endings.

## Model Library (0.9.0)

Version 0.9.1 uses a charcoal workspace inspired by [Adobe Spectrum's background layers](https://spectrum.adobe.com/foundations/color/background-layers): distinct Model Target, Model Library, and Model Editor panels, clearer table headers and selections, blue focus/primary actions, and a compact Output panel. The same palette is applied to all four workspaces, with visible disabled controls and existing status colors.

1. Choose `Model > Model Library > Index Model Folder...`, select the parent of your manufacturer/part-number folders, then choose where to save the `.sqlite` index. Subfolders are included; indexing runs in the background.
2. Search above the model editor using a part number, manufacturer, package size, capacitance, or several terms together (for example `Murata 0402 100nF`). Every term must match. Searches ignore case and recognize equivalent capacitance units such as `0.1 uF`, `100 nF`, and `100000 pF`, including `µF`/`μF`.
3. Select the target PartialCkt and double-click a result, press Enter in the search field, or click `Load Selected`. The app reads the current source model and uses the same port validation/conversion as file import. Hover over the result to inspect its path and comments. The first 200 matches are shown; refine the search to narrow the list.

The `Model Library` menu also provides:

- `Open Model Index...`: load an existing index. The last successfully opened/saved index is restored on the next launch.
- `Save Model Index As...`: save a copy elsewhere and switch to it. The original index remains available.
- `Update Model Index`: rescan the original folder, incorporating added, changed, and deleted files. If that folder has moved, choose its new location when prompted.
- `Cancel Library Operation`: stop the current index, open, or save operation. Failed/cancelled operations leave the previous index intact.

Opening, restoring, and saving indexes also run in the background so large libraries do not block the interface.

Indexes can be saved beside a writable executable or in any writable folder. A user documents folder is the default; installed applications under `Program Files` may not be writable. Avoid the application's `_internal` folder, which the installer replaces during upgrades. The index stores model paths and searchable metadata, **not copies of the source models**. Keep the model folder available to load a result. If using the index on another PC or after moving the models, use `Update Model Index` to select the new root when the old root is unavailable. Index files contain local paths and vendor comments; share them only when appropriate.

Supported candidates are `.mod`, `.lib`, `.cir`, `.sp`, `.spi`, `.spice`, `.ckt`, `.net`, `.txt`, and `.inc` files containing a `.SUBCKT` header. PDF datasheets, archives, binaries, symlink/junction directories, and files larger than 8 MiB are excluded. Full-line and inline SPICE comments are searched; datasheet OCR and ZIP extraction are not included. A match in the library does not guarantee that the existing SPICE converter supports its dialect; unsupported/multiple subcircuits still fail validation before the editor is replaced. Updates are explicit, rather than a continuous folder watcher.

## Generate Port

The menu bar is organized as `File`, `Edit`, `Model`, `Port`, `DC`, `VRM/Sink`, `View`, and `Help`. The top tabs provide separate `Model & RefDes`, `Port Generation`, `DC Setting`, and `VRM/Sink Setting` workspaces. The Port workspace is split left/right: select one or more Power channels on the left, expand the Component→RefDes tree, select the required RefDes rows, and choose `Generate Port`. `DGND` is used automatically when present; only files without `DGND` prompt for an exact reference NET. The right side lists existing and pending Ports with pin counts, activation checkboxes, and deletion/restore controls. `Export New SPD` applies the queued changes without modifying the source file.

In 0.6.0, `Ctrl+F` focuses the component search in `Model & RefDes`, or the Power NET search in `Port Generation` (and, since 0.7.0, the channel search in `DC Setting`; since 0.8.0, the VRM/Sink search in `VRM/Sink Setting`). Power NET search is case-insensitive and filters the displayed list without changing checked targets. The summary shows how many NETs are visible and how many are checked, including hidden items. Clear the search to see all NETs again; loading a new SPD resets the search. The separate RefDes instance search remains available below it.

Files without Ports are supported: export creates a `.Port`/`.EndPort` section at the reserved Port location, or before the valid `.NetList` section if no reserved location exists. Malformed or multiple Port sections remain blocked. Select the SITE0/SITE1 child rows under DUT after checking their Power NETs.

## DC Setting (0.7.0)

The `DC Setting` tab prepares a PowerSI board for PowerDC. It lists every `PowerNets` member of `.NetList` (active and inactive) with its current `Voltage` and `GroundNet`. Search filters the list, and rows support multi-selection (`Ctrl`/`Shift`-click, or `Ctrl+A` on the filtered list). Enter a `Pairing P/G NET` (defaults to `DGND` when present) and `Volt (V)`, then `Apply to Selected` to queue the setting for every selected visible row; `Auto-fill Selected` reads the voltage from each NET name instead (`VDD085` → 0.85 V, `VDD075` → 0.75 V, `VDD18` → 1.8 V, `VDD12` → 1.2 V, `1V8`/`0P75`/`1.2V` styles; 1 V when nothing looks like a voltage). Pending rows are highlighted and can be reverted or cleared before export.

On export each queued NET line gains `Voltage = <V> GroundNet = <NET>`, replacing any previous values, exactly as PowerDC writes them:

```text
	ADC_VDDI_TRIP0/0 Color = BLUE Voltage = 0.95 GroundNet = DGND
```

Applying a setting to an inactive (`::Unselected`) NET also activates it, because PowerDC only simulates active nets. The pairing NET must be an active NET in `.NetList`. Nothing else in the file is touched; the NetList is neither reordered nor re-flagged. Files saved by PowerDC (`||DropShape` flags on NET and group tokens) are now parsed correctly, so their Power NETs and `DGND` are available in `Port Generation` and `DC Setting`.

## VRM/Sink Setting (0.8.0)

The `VRM/Sink Setting` tab edits the VRMs and Sinks that PowerDC has already saved into the SPD. Every `.VRM` / `.Sink` block is listed with its type, name, and the `Key = Value` properties found on its header line (`NominalVoltage`, `SenseVoltage`, `OutputCurrent` for VRMs; `NominalVoltage`, `Current`, `Model`, `PFMode`, `PinEqualCurrent` for Sinks). Search filters by type or name, and rows support multi-selection (`Ctrl`/`Shift`-click, or `Ctrl+A` on the filtered list). Pick a `Property`, enter the new `Value`, then `Apply to Selected` to queue it for every selected visible row; rows that do not carry the chosen property are skipped, and values identical to the file are not queued. Repeat with another property to change several at once. Pending rows are highlighted and can be reverted or cleared before export.

On export only the value token of each queued property is replaced on the header line; the pin mappings inside the block and everything else in the file are untouched:

```text
.VRM NominalVoltage = 0.9 SenseVoltage = 0.9 OutputCurrent = 1 Name = "VRM_LGA_ADC_VDD_CPU/0_DGND"
.Sink NominalVoltage = 0.9 Current = 2.5 Model = 2 PFMode = 2 PinEqualCurrent = 1 Name = "SINK_SITE1_ADC_VDD_CPU/1_DGND"
```

The tab only changes properties that already exist on the header; it does not create VRMs or Sinks. Use PowerDC to define them first, save the SPD, and load that file here.

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

The application icon (a SPICE model being injected into a PowerSI board) is drawn in `packaging/icon.svg`. After editing it, regenerate the shipped `app.ico`/`app.png` with:

```powershell
python scripts/make_icon.py
```

## Repository Safety

Large board files and vendor model files can contain sensitive design data, so `*.spd` and `*.mod` are ignored by default. Keep sample boards outside Git history and attach release artifacts through GitHub Releases instead.
