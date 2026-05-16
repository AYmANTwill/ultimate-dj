@echo off
:: One-shot Windows build — produces dist\UltimateDJ\UltimateDJ.exe
:: ----------------------------------------------------------------
:: Prerequisites: Python 3.10+, project deps installed, FFmpeg + Node
:: present at runtime (the splash will install them on the user's box
:: if missing — they're not bundled to keep the .exe under 250 MB).
::
:: Usage:    build.bat
:: Output:   dist\UltimateDJ\          (folder to ship)
::
title Ultimate DJ - Build
color 0B

echo.
echo  ==============================================
echo       Ultimate DJ - PyInstaller build
echo  ==============================================
echo.

where python.exe >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] Python not found on PATH
    pause
    exit /b 1
)

echo  Step 1/3  - Ensuring PyInstaller is installed...
python -m pip install --quiet --upgrade pyinstaller || (
    echo  [ERROR] pip install pyinstaller failed
    pause
    exit /b 1
)

echo  Step 2/3  - Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo  Step 3/3  - Running PyInstaller...
python -m PyInstaller --clean --noconfirm ultimate_dj.spec
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] PyInstaller build failed
    pause
    exit /b 1
)

echo.
echo  --------------------------------------------------
echo  Build OK -- dist\UltimateDJ\UltimateDJ.exe
echo  --------------------------------------------------
echo.
echo  Test on a clean machine before shipping. Splash
echo  will auto-install missing FFmpeg / Node via winget
echo  on first launch.
echo.
pause
