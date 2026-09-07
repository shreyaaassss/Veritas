@echo off
REM Veritas Agent — Windows Install Script
REM ========================================
REM Installs the Veritas Agent as a Windows Service.
REM Run as Administrator from the veritas-agent\ directory.
REM
REM Prerequisites:
REM   - Python 3.10+ installed and in PATH
REM   - agent-config.yaml filled in (veritas_address + registration_key)
REM
REM Usage:
REM   deploy\install-agent-windows.bat

setlocal
cd /d "%~dp0.."

echo.
echo ============================================
echo   Veritas Agent -- Windows Installation
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ from https://python.org
    exit /b 1
)

echo [1/5] Installing Python dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    exit /b 1
)

echo [2/5] Installing pywin32 (Windows Service support)...
pip install pywin32 --quiet
if errorlevel 1 (
    echo ERROR: Failed to install pywin32.
    exit /b 1
)

echo [3/5] Running pywin32 post-install...
python -c "import win32serviceutil" >nul 2>&1
if errorlevel 1 (
    python Scripts\pywin32_postinstall.py -install >nul 2>&1
)

REM Check config exists
if not exist "agent-config.yaml" (
    echo.
    echo WARNING: agent-config.yaml not found.
    echo Copying example config...
    copy /Y "agent-config.yaml.example" "agent-config.yaml" >nul
    echo.
    echo ACTION REQUIRED: Edit agent-config.yaml and set:
    echo   veritas_address  -- URL of your Veritas Server
    echo   registration_key -- from Dashboard ^> Agents ^> Issue Registration Key
    echo.
    echo Then run this installer again.
    echo.
    pause
    exit /b 1
)

echo [4/5] Downloading TLS certificate from server...
for /f "tokens=2 delims=: " %%a in ('findstr "veritas_address" agent-config.yaml') do set SERVER=%%a
if defined SERVER (
    curl -sk "%SERVER%/api/tls/cert" -o "server.crt" 2>nul
    if exist "server.crt" (
        findstr /c:"BEGIN CERTIFICATE" "server.crt" >nul 2>&1
        if not errorlevel 1 (
            echo     [OK] TLS certificate saved to server.crt
        ) else (
            del /f "server.crt" 2>nul
            echo     WARNING: Could not download TLS certificate.
        )
    )
)

echo [5/5] Installing Windows Service...

REM Stop and remove if already exists
sc query VeritasAgent >nul 2>&1
if not errorlevel 1 (
    echo     Removing existing service...
    python windows_service.py stop >nul 2>&1
    python windows_service.py remove >nul 2>&1
    timeout /t 2 >nul
)

REM Install the service
python windows_service.py install
if errorlevel 1 (
    echo ERROR: Failed to install Windows Service.
    exit /b 1
)

REM Set service description and auto-restart on failure
sc description VeritasAgent "Veritas DPDPA compliance telemetry forwarder" >nul
sc failure VeritasAgent reset= 60 actions= restart/10000/restart/10000/restart/30000 >nul

REM Start the service
python windows_service.py start
if errorlevel 1 (
    echo WARNING: Service installed but could not start automatically.
    echo          Check agent-config.yaml and try: python windows_service.py start
) else (
    echo     [OK] Service started successfully.
)

echo.
echo ============================================
echo   Installation complete
echo ============================================
echo.
echo   Service:  VeritasAgent
echo   Status:   python windows_service.py status
echo   Stop:     python windows_service.py stop
echo   Logs:     Event Viewer ^> Windows Logs ^> Application ^> VeritasAgent
echo.
echo   The agent will start automatically on next Windows boot.
echo ============================================
echo.

endlocal
