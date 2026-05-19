@echo off
REM ============================================================================
REM Hermes Agent Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users running CMD.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
<<<<<<< HEAD
REM   irm https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.ps1 | iex
=======
REM   iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
>>>>>>> origin/main
REM ============================================================================

echo.
echo  Hermes Agent Installer
echo  Launching PowerShell installer...
echo.

<<<<<<< HEAD
powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.ps1 | iex"
=======
powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)"
>>>>>>> origin/main

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
<<<<<<< HEAD
    echo    powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.ps1 | iex"
=======
    echo    powershell -ExecutionPolicy ByPass -c "iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)"
>>>>>>> origin/main
    echo.
    pause
    exit /b 1
)
