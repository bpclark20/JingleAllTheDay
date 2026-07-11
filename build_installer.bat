@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "OUT_ROOT=%LOCALAPPDATA%\JingleAllTheDay\pyinstaller"
set "SOURCE_DIR="
set "SCRIPT_PATH=%~dp0installer.iss"
set "APP_VERSION="
set "OUTPUT_DIR=%~dp0installer"
set "ISCC_PATH="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="-OutRoot" (
    if "%~2"=="" goto missing_outroot
    set "OUT_ROOT=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-SourceDir" (
    if "%~2"=="" goto missing_sourcedir
    set "SOURCE_DIR=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-ScriptPath" (
    if "%~2"=="" goto missing_scriptpath
    set "SCRIPT_PATH=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-AppVersion" (
    if "%~2"=="" goto missing_appversion
    set "APP_VERSION=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-OutputDir" (
    if "%~2"=="" goto missing_outputdir
    set "OUTPUT_DIR=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-IsccPath" (
    if "%~2"=="" goto missing_isccpath
    set "ISCC_PATH=%~2"
    shift
    shift
    goto parse_args
)
echo Unknown argument: %~1
goto usage

:missing_outroot
echo Missing value for -OutRoot
goto usage
:missing_sourcedir
echo Missing value for -SourceDir
goto usage
:missing_scriptpath
echo Missing value for -ScriptPath
goto usage
:missing_appversion
echo Missing value for -AppVersion
goto usage
:missing_outputdir
echo Missing value for -OutputDir
goto usage
:missing_isccpath
echo Missing value for -IsccPath
goto usage

:args_done
if "%SOURCE_DIR%"=="" set "SOURCE_DIR=%OUT_ROOT%\dist"

if not exist "%SCRIPT_PATH%" (
    echo Installer script not found: %SCRIPT_PATH%
    exit /b 1
)

if not exist "%SOURCE_DIR%" (
    echo Source directory not found: %SOURCE_DIR%
    echo.
    echo Tip: Run .\build_exe.bat first, or pass -SourceDir explicitly.
    exit /b 1
)

if not exist "%SOURCE_DIR%\JingleAllTheDay.exe" (
    echo Expected bundled EXE not found: %SOURCE_DIR%\JingleAllTheDay.exe
    exit /b 1
)

if "%APP_VERSION%"=="" (
    call :infer_app_version
    if errorlevel 1 exit /b %ERRORLEVEL%
)

if "%ISCC_PATH%"=="" (
    call :find_iscc
    if errorlevel 1 exit /b %ERRORLEVEL%
)

if not exist "%ISCC_PATH%" (
    echo Inno Setup compiler not found: %ISCC_PATH%
    exit /b 1
)

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
if errorlevel 1 (
    echo Failed to create output directory: %OUTPUT_DIR%
    exit /b 1
)

for %%I in ("%SOURCE_DIR%") do set "JATD_SOURCE_DIR=%%~fI"
for %%I in ("%OUTPUT_DIR%") do set "RESOLVED_OUTPUT_DIR=%%~fI"

"%ISCC_PATH%" "/DMyAppVersion=%APP_VERSION%" "/DMyOutputDir=%RESOLVED_OUTPUT_DIR%" "%SCRIPT_PATH%"
if errorlevel 1 (
    echo ISCC failed with exit code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo Installer build complete.
echo Version: %APP_VERSION%
echo Output folder: %RESOLVED_OUTPUT_DIR%
exit /b 0

:infer_app_version
set "APP_PATH=%~dp0app.py"
if not exist "%APP_PATH%" (
    echo Cannot infer app version because app.py was not found. Provide -AppVersion explicitly.
    exit /b 1
)

set "APP_VERSION="
for /f "usebackq delims=" %%V in (`"%SystemRoot%\System32\findstr.exe" /R /C:"APP_VERSION[ ]*=" "%APP_PATH%"`) do (
    set "LINE=%%V"
    call :extract_double_quoted_version "!LINE!"
    if not "!APP_VERSION!"=="" goto inferred_ok
    call :extract_single_quoted_version "!LINE!"
    if not "!APP_VERSION!"=="" goto inferred_ok
)

echo Unable to parse APP_VERSION from app.py. Provide -AppVersion explicitly.
exit /b 1

:inferred_ok
exit /b 0

:extract_double_quoted_version
setlocal EnableDelayedExpansion
set "RAW=%~1"
set "VER="
set "RHS="
for /f "tokens=2* delims==" %%A in ("!RAW!") do set "RHS=%%A"
if defined RHS for /f "tokens=1 delims=#" %%B in ("!RHS!") do set "RHS=%%B"
if defined RHS (
    for /f "tokens=2 delims=\"" %%C in ("!RHS!") do set "VER=%%C"
)
endlocal & set "APP_VERSION=%VER%"
exit /b 0

:extract_single_quoted_version
setlocal EnableDelayedExpansion
set "RAW=%~1"
set "VER="
set "RHS="
for /f "tokens=2* delims==" %%A in ("!RAW!") do set "RHS=%%A"
if defined RHS for /f "tokens=1 delims=#" %%B in ("!RHS!") do set "RHS=%%B"
if defined RHS (
    for /f "tokens=2 delims='" %%C in ("!RHS!") do set "VER=%%C"
)
endlocal & set "APP_VERSION=%VER%"
exit /b 0

:find_iscc
set "ISCC_PATH="

if defined ProgramFiles if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%LOCALAPPDATA%\Inno Setup 6\ISCC.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ISCC.exe" set "ISCC_PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links\ISCC.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

if defined ChocolateyInstall if exist "%ChocolateyInstall%\bin\iscc.exe" set "ISCC_PATH=%ChocolateyInstall%\bin\iscc.exe"
if not "%ISCC_PATH%"=="" goto find_iscc_done

for /f "usebackq delims=" %%I in (`where iscc.exe 2^>nul`) do (
    set "ISCC_PATH=%%~fI"
    goto find_iscc_done
)

call :find_iscc_winget
if not "%ISCC_PATH%"=="" goto find_iscc_done

echo Inno Setup compiler ^(ISCC.exe^) was not found.
echo.
echo Install it, then run this script again:
echo   winget install --exact --id JRSoftware.InnoSetup
echo.
echo If already installed, close and reopen your terminal so PATH updates are picked up.
exit /b 1

:find_iscc_done
exit /b 0

:find_iscc_winget
if not defined LOCALAPPDATA exit /b 0
set "WINGET_ROOT=%LOCALAPPDATA%\Microsoft\WinGet\Packages"
if not exist "%WINGET_ROOT%" exit /b 0

for /d %%D in ("%WINGET_ROOT%\JRSoftware.InnoSetup*") do (
    if exist "%%~fD\ISCC.exe" (
        set "ISCC_PATH=%%~fD\ISCC.exe"
        exit /b 0
    )
    if exist "%%~fD\tools\ISCC.exe" (
        set "ISCC_PATH=%%~fD\tools\ISCC.exe"
        exit /b 0
    )
)

exit /b 0

:usage
echo Usage:
echo   build_installer.bat [-OutRoot path] [-SourceDir path] [-ScriptPath path] [-AppVersion version] [-OutputDir path] [-IsccPath path]
exit /b 1
