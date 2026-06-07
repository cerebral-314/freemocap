@echo off
setlocal

cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "FREEMOCAP_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%FREEMOCAP_PYTHON%" (
    echo FreeMoCap virtual environment was not found:
    echo   %FREEMOCAP_PYTHON%
    echo.
    echo Create or install the local .venv before running this launcher.
    pause
    exit /b 1
)

"%FREEMOCAP_PYTHON%" -X utf8 -m freemocap %*
set "FREEMOCAP_EXIT_CODE=%ERRORLEVEL%"

if not "%FREEMOCAP_EXIT_CODE%"=="0" (
    echo.
    echo FreeMoCap exited with code %FREEMOCAP_EXIT_CODE%.
    pause
)

exit /b %FREEMOCAP_EXIT_CODE%
