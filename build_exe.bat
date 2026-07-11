@echo off
setlocal EnableExtensions

set "OUT_ROOT=%LOCALAPPDATA%\JingleAllTheDay\pyinstaller"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="-OutRoot" (
    if "%~2"=="" goto missing_outroot
    set "OUT_ROOT=%~2"
    shift
    shift
    goto parse_args
)
echo Unknown argument: %~1
goto usage

:missing_outroot
echo Missing value for -OutRoot
goto usage

:args_done
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SPEC_PATH=%PROJECT_ROOT%\JingleAllTheDay.spec"
set "DIST_PATH=%OUT_ROOT%\dist"
set "WORK_PATH=%OUT_ROOT%\build"

if not exist "%PYTHON_EXE%" (
    echo Python executable not found: %PYTHON_EXE%
    exit /b 1
)

if not exist "%SPEC_PATH%" (
    echo Spec file not found: %SPEC_PATH%
    exit /b 1
)

if not exist "%DIST_PATH%" mkdir "%DIST_PATH%"
if errorlevel 1 (
    echo Failed to create dist path: %DIST_PATH%
    exit /b 1
)

if not exist "%WORK_PATH%" mkdir "%WORK_PATH%"
if errorlevel 1 (
    echo Failed to create work path: %WORK_PATH%
    exit /b 1
)

"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --distpath "%DIST_PATH%" --workpath "%WORK_PATH%" "%SPEC_PATH%"
if errorlevel 1 (
    echo PyInstaller failed with exit code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

set "BUNDLE_DIR=%DIST_PATH%\JingleAllTheDay"
if exist "%BUNDLE_DIR%" (
    if exist "%DIST_PATH%\JingleAllTheDay.exe" del /f /q "%DIST_PATH%\JingleAllTheDay.exe"
    if exist "%DIST_PATH%\_internal" rmdir /s /q "%DIST_PATH%\_internal"

    move /y "%BUNDLE_DIR%\*" "%DIST_PATH%\" >nul
    if errorlevel 1 (
        echo Failed to flatten bundle directory: %BUNDLE_DIR%
        exit /b %ERRORLEVEL%
    )

    rmdir /s /q "%BUNDLE_DIR%"
)

if exist "%PROJECT_ROOT%\rev.log" (
    copy /y "%PROJECT_ROOT%\rev.log" "%DIST_PATH%\" >nul
    if errorlevel 1 (
        echo Failed to copy rev.log to %DIST_PATH%
        exit /b %ERRORLEVEL%
    )
    echo Copied rev.log to %DIST_PATH%
)

echo Build complete.
echo Output: %DIST_PATH%
exit /b 0

:usage
echo Usage:
echo   build_exe.bat [-OutRoot "C:\path\to\pyinstaller"]
exit /b 1
