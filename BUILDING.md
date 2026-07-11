# JingleAllTheDay Build and Installer Guide

This file is the dedicated reference for controlling build inputs and outputs.

## Quick Start (Default Paths)

1. Build the app bundle:

```bat
build_exe.bat
```

2. Build the installer from that bundle:

```bat
build_installer.bat
```

Default result:
- App bundle output: `%LOCALAPPDATA%\JingleAllTheDay\pyinstaller\dist`
- Installer output folder: `./installer`

## Script 1: build_exe.bat

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

```bat
build_exe.bat
```

Custom output root:

```bat
build_exe.bat -OutRoot "D:\build-cache\JATD"
```

## Script 2: build_installer.bat

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

```bat
build_installer.bat
```

Use custom source dir:

```bat
build_installer.bat -SourceDir "D:\build-cache\JATD\dist"
```

Set version manually:

```bat
build_installer.bat -AppVersion "1.2.3-custom"
```

Set custom output folder:

```bat
build_installer.bat -OutputDir "D:\releases\JATD"
```

Provide explicit Inno Setup compiler path:

```bat
build_installer.bat -IsccPath "C:\Program Files\Inno Setup 6\ISCC.exe"
```

Full custom example:

```bat
build_installer.bat -OutRoot "D:\build-cache\JATD" -SourceDir "D:\build-cache\JATD\dist" -ScriptPath ".\installer.iss" -AppVersion "1.2.3" -OutputDir "D:\releases\JATD"
```

## Recommended Release Flow

```bat
build_exe.bat -OutRoot "D:\build-cache\JATD"
build_installer.bat -OutRoot "D:\build-cache\JATD" -OutputDir "D:\releases\JATD"
```

## Troubleshooting

If installer build says source directory not found:
- Run `build_exe.bat` first, or pass `-SourceDir` explicitly.

If installer build says expected EXE not found:
- Verify `JingleAllTheDay.exe` exists in your `SourceDir`.

If Inno Setup compiler not found:
- Install Inno Setup:

```bat
winget install --exact --id JRSoftware.InnoSetup
```

- Then restart terminal/VS Code so PATH updates are visible.
- Or run with `-IsccPath` pointing directly to `ISCC.exe`.

If app version cannot be parsed:
- Pass `-AppVersion` explicitly.
