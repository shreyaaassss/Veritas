@echo off
REM ============================================================
REM Veritas — Windows Build Script
REM Produces: dist\veritas-runtime.exe
REM ============================================================
REM
REM Prerequisites:
REM   pip install pyinstaller
REM   pip install -r requirements.txt
REM   python -m spacy download en_core_web_lg
REM
REM Usage:
REM   cd dpdpa-agent
REM   build_windows.bat

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo  Veritas Runtime — Windows Build
echo ============================================================
echo.

REM Check PyInstaller is available
pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller --quiet
)

REM Clean previous output
if exist "dist\veritas-runtime.exe" (
    echo [INFO] Removing previous build...
    del /f "dist\veritas-runtime.exe" 2>nul
)
if exist "build" (
    rmdir /s /q "build" 2>nul
)

echo [INFO] Running PyInstaller...
echo.
pyinstaller veritas.spec --noconfirm

echo.
if exist "dist\veritas-runtime.exe" (
    echo ============================================================
    echo  BUILD SUCCESSFUL
    echo ============================================================
    echo  Output: dist\veritas-runtime.exe
    for %%A in ("dist\veritas-runtime.exe") do echo  Size:   %%~zA bytes
    echo.
    echo  Next steps:
    echo    1. Copy dist\veritas-runtime.exe to veritas-launcher\dist\
    echo    2. Build the Go launcher: cd ..\veritas-launcher ^&^& go build
    echo    3. Build the installer: iscc veritas-installer.iss
    echo ============================================================
) else (
    echo ============================================================
    echo  BUILD FAILED — check output above for errors
    echo ============================================================
    exit /b 1
)

endlocal
