# JingleAllTheDay Portability Handoff Plan

This file summarizes the Linux portability work done in this session and the remaining steps for future validation on Linux and Windows.

## Goal

Preserve existing Windows behavior while adding Linux support (Kubuntu/Ubuntu/Debian-like) with best-effort Wayland support.

## Completed In This Session

1. Linux and cross-platform runtime resilience
- Made sample-pad backend import resilient when system PortAudio is missing (no startup crash).
- Added one-time in-app warning when sample-pad low-latency backend is unavailable.
- Added Wayland-aware global hotkey status messages.

2. Sample-pad device routing fix
- Improved sample-pad device matching in PortAudio lookup.
- Prevented silent fallback to system default when a specific sample-pad device cannot be resolved.
- Added fallback to standard playback path when low-latency sample-pad routing cannot open selected device.

3. Startup/platform boundary hardening
- Guarded Win32 AppUserModelID startup call so startup does not fail if shell API is unavailable.
- Hardened Windows taskbar icon helper to avoid blocking startup on Win32 API failures.
- Added fallback app-data path for single-instance lock if Qt AppDataLocation is empty.

4. Linux packaging and docs
- Added Linux build helper script: build_linux.sh
- Updated README with Linux install/run/build steps.
- Added support matrix and Linux package prerequisites.

5. Utility cleanup
- Removed hardcoded Windows path assumptions from analyze.py and added CLI args.

## Files Changed

- README.md
- analyze.py
- app.py
- app_helpers.py
- gui.py
- sample_pad_audio_engine.py
- build_linux.sh (new)

## Remaining Plan Steps

1. Linux validation sweep (manual + scripted)
- Verify first-run folder selection and settings persistence.
- Verify main playback device routing for Live/Preview mode.
- Verify sample-pad routing in Live/Preview modes with same and different devices.
- Verify global hotkeys with pynput in both X11 and Wayland sessions.
- Verify behavior when PortAudio is unavailable and warning is shown once.

2. Linux packaging smoke test
- Build bundle using build_linux.sh.
- Launch built artifact and confirm startup + settings path behavior.

3. Windows regression sanity pass (deferred)
- Run app.py on Windows.
- Verify taskbar icon behavior and single-instance behavior.
- Verify global hotkeys (pynput + Windows-native fallback).
- Verify installer and build scripts remain functional.

## Validation Commands (Linux)

Use project venv:

```bash
source .venv/bin/activate
```

Syntax/import checks:

```bash
python3 -m py_compile app.py app_helpers.py gui.py sample_pad_audio_engine.py analyze.py
```

Quick backend checks:

```bash
python3 -c "import sample_pad_audio_engine as s; print('sample-pad backend available:', s.is_available())"
python3 -c "import app_helpers, app, gui; print('imports ok')"
```

Build helper syntax check:

```bash
bash -n build_linux.sh
```

Linux package prerequisites for low-latency sample-pad backend:

```bash
sudo apt update
sudo apt install -y libportaudio2 libsndfile1
```

Bundle build:

```bash
bash ./build_linux.sh
```

## Current Validation Status

- Python syntax/import compile checks pass on Linux.
- Linux PyInstaller one-folder build passes via build_linux.sh.
- Current Linux bundle output path: dist/JingleAllTheDay/
- Observed PyInstaller warning on Linux build host:
  - missing libtiff.so.5 dependency for Qt tiff imageformat plugin.
  - This is non-blocking for core audio/jingle workflows unless TIFF image plugin support is needed.
- Windows regression testing is still pending because no Windows host is currently available.

## Windows Follow-Up Checklist

When back on Windows, validate in this order:

1. Create venv and install requirements.
2. Run app.py and verify startup/single-instance behavior.
3. Verify main playback routing and sample-pad routing.
4. Verify global hotkeys (native and fallback behavior).
5. Run build_exe.bat.
6. Run build_installer.bat.

## Notes

- If sample-pad routing issues appear on a specific host, add temporary trigger-time logging for:
  - requested device label
  - resolved PortAudio device index/name
  - fallback-path reason
- This can isolate backend naming mismatches quickly per machine.
