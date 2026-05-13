@echo off
title Ultimate DJ - Launching...
color 0A

echo.
echo  ============================================
echo       ULTIMATE DJ TOOL - Launcher
echo  ============================================
echo.

:: Try to find Python 3.11+
set "PY="
where python3.11.exe >nul 2>&1 && set "PY=python3.11.exe" && goto :found
where python3.exe >nul 2>&1 && set "PY=python3.exe" && goto :found
where python.exe >nul 2>&1 && set "PY=python.exe" && goto :found

echo  [ERROR] Python not found. Install Python 3.11+ from python.org
echo.
pause
exit /b 1

:found
echo  Python: %PY%

:: Run the app (deps.py handles auto-install)
%PY% "%~dp0run.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [ERROR] App exited with error code %ERRORLEVEL%
    echo  Check the console output above for details.
    echo.
    pause
)
