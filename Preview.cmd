@echo off
cd /d "%~dp0"
if exist "%USERPROFILE%\miniconda3\python.exe" (
    "%USERPROFILE%\miniconda3\python.exe" app.py %*
) else (
    python app.py %*
)
if errorlevel 1 pause
