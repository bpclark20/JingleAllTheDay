# JingleAllTheDay Build and Installer Guide

This file is the dedicated reference for controlling build inputs and outputs.

## Quick Start (Default Paths)

1. Build the app bundle:

```powershell
.\build_exe.ps1
```

2. Build the installer from that bundle:

```powershell
.\build_installer.ps1
```

Default result:
- App bundle output: `%LOCALAPPDATA%\JingleAllTheDay\pyinstaller\dist`
- Installer output folder: `./installer`

## Remote Control Server Note

The app bundles a LAN remote-control server (FastAPI/Uvicorn) that auto-starts on a
non-standard port (Options > Server Port, default `8765`). On first run of a packaged
build, Windows Defender Firewall may prompt to allow the app to accept inbound
connections on the private network — allow it if remote control from a phone/browser
on the LAN is desired, or leave the "Enable remote server" option in Options unchecked
to disable auto-start entirely.

## Script 1: build_exe.ps1

Purpose:
- Runs PyInstaller with `JingleAllTheDay.spec` using project virtualenv Python.
- Writes build artifacts under an output root.
- Flattens one-folder output so `JingleAllTheDay.exe` and `_internal` sit directly under `dist`.

### Inputs

Parameter:
- `-OutRoot`
  - Type: string
  - Default: `%LOCALAPPDATA%\JingleAllTheDay\pyinstaller`
  - Controls where `dist` and `build` folders are created.

Implicit inputs:
- `./.venv/Scripts/python.exe` (must exist)
- `./JingleAllTheDay.spec` (must exist)
- Optional: `./rev.log` (copied into dist if present)

### Outputs

Given `OutRoot = X`:
- Dist output folder: `X\dist`
- Work/cache folder: `X\build`
- Main EXE expected at: `X\dist\JingleAllTheDay.exe`
- Runtime support folder expected at: `X\dist\_internal`
- Optional copied file: `X\dist\rev.log`

### Common Invocations

Default:

```powershell
.\build_exe.ps1
```

Custom output root:

```powershell
.\build_exe.ps1 -OutRoot "D:\build-cache\JATD"
```

## Script 2: build_installer.ps1

Purpose:
- Compiles `installer.iss` with Inno Setup (`ISCC.exe`).
- Uses app bundle files from a source directory.
- Supports explicit versioning or auto-version from `app.py`.

### Inputs

Parameters:
- `-OutRoot`
  - Type: string
  - Default: `%LOCALAPPDATA%\JingleAllTheDay\pyinstaller`
  - Used to derive default `SourceDir` if not provided.
- `-SourceDir`
  - Type: string
  - Default: `<OutRoot>\dist`
  - Must contain `JingleAllTheDay.exe`.
- `-ScriptPath`
  - Type: string
  - Default: `./installer.iss`
  - Inno Setup script path.
- `-AppVersion`
  - Type: string
  - Default: empty (auto-detect)
  - If empty, script parses `APP_VERSION` from `./app.py`.
- `-OutputDir`
  - Type: string
  - Default: `./installer`
  - Directory where installer executable is written.
- `-IsccPath`
  - Type: string
  - Default: empty (auto-discover)
  - Optional full path to `ISCC.exe` to bypass auto-discovery.

Implicit inputs:
- Inno Setup compiler `ISCC.exe` (auto-discovered from common install locations, PATH, registry, or WinGet package folder)

Environment variable set by script before invoking Inno Setup:
- `JATD_SOURCE_DIR` = resolved SourceDir path

### Outputs

- Installer output folder: resolved `OutputDir`
- Installer executable filename is determined by `installer.iss` settings (currently expected pattern: `JingleAllTheDay-Setup-<version>.exe`)
- Console output includes:
  - `Version: <AppVersion>`
  - `Output folder: <resolvedOutputDir>`

### Common Invocations

Default (uses `%LOCALAPPDATA%\JingleAllTheDay\pyinstaller\dist`):

```powershell
.\build_installer.ps1
```

Use custom source dir:

```powershell
.\build_installer.ps1 -SourceDir "D:\build-cache\JATD\dist"
```

Set version manually:

```powershell
.\build_installer.ps1 -AppVersion "1.2.3-custom"
```

Set custom output folder:

```powershell
.\build_installer.ps1 -OutputDir "D:\releases\JATD"
```

Provide explicit Inno Setup compiler path:

```powershell
.\build_installer.ps1 -IsccPath "C:\Program Files\Inno Setup 6\ISCC.exe"
```

Full custom example:

```powershell
.\build_installer.ps1 -OutRoot "D:\build-cache\JATD" -SourceDir "D:\build-cache\JATD\dist" -ScriptPath ".\installer.iss" -AppVersion "1.2.3" -OutputDir "D:\releases\JATD"
```

## Recommended Release Flow

```powershell
.\build_exe.ps1 -OutRoot "D:\build-cache\JATD"
.\build_installer.ps1 -OutRoot "D:\build-cache\JATD" -OutputDir "D:\releases\JATD"
```

## Troubleshooting

If installer build says source directory not found:
- Run `./build_exe.ps1` first, or pass `-SourceDir` explicitly.

If installer build says expected EXE not found:
- Verify `JingleAllTheDay.exe` exists in your `SourceDir`.

If Inno Setup compiler not found:
- Install Inno Setup:

```powershell
winget install --exact --id JRSoftware.InnoSetup
```

- Then restart terminal/VS Code so PATH updates are visible.
- Or run with `-IsccPath` pointing directly to `ISCC.exe`.

If app version cannot be parsed:
- Pass `-AppVersion` explicitly.
