@echo off
REM ============================================================
REM Veritas Launcher — Windows Build Script
REM Produces: dist\veritas-launcher.exe
REM ============================================================
REM
REM Prerequisites: Go 1.21+ installed (go.dev/dl)
REM
REM Usage:
REM   cd veritas-launcher
REM   build.bat

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo  Veritas Launcher — Go Build
echo ============================================================
echo.

REM Check Go is available
go version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Go is not installed or not in PATH.
    echo Download from: https://go.dev/dl/
    exit /b 1
)
go version

REM Download dependencies
echo.
echo [INFO] Downloading Go dependencies...
go mod tidy
if errorlevel 1 (
    echo ERROR: go mod tidy failed.
    exit /b 1
)

REM Create output directory
if not exist "..\dpdpa-agent\dist" mkdir "..\dpdpa-agent\dist"

REM Build for Windows x64
REM -ldflags="-H windowsgui" would hide the console window — we keep console
REM for service logging. Use -s -w to strip debug symbols (smaller binary).
echo.
echo [INFO] Building veritas-launcher.exe...
set GOOS=windows
set GOARCH=amd64
go build -ldflags="-s -w" -o "..\dpdpa-agent\dist\veritas-launcher.exe" .
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  BUILD SUCCESSFUL
echo ============================================================
for %%A in ("..\dpdpa-agent\dist\veritas-launcher.exe") do echo  Output: %%~fA  (%%~zA bytes)
echo.
echo  Commands (run as Administrator):
echo    veritas-launcher.exe install    -- register Windows Service
echo    veritas-launcher.exe start      -- start service
echo    veritas-launcher.exe stop       -- stop service
echo    veritas-launcher.exe uninstall  -- remove service
echo    veritas-launcher.exe run        -- run directly (debug mode)
echo ============================================================
endlocal
