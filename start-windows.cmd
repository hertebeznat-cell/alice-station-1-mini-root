@echo off
cd /d "%~dp0"
if exist "dist\StationMiniRoot.exe" (
    start "" "dist\StationMiniRoot.exe"
    exit /b
)
py -3 station_mini_root.py
if errorlevel 1 pause
