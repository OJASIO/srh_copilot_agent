@echo off
REM One-time setup: creates the virtual environment and installs dependencies.
cd /d "%~dp0"
echo Creating virtual environment...
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
echo Installing dependencies (this takes a few minutes)...
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
echo.
echo Setup done. Now paste your Gemini key into the .env file,
echo then run start_api.bat and start_ui.bat in two separate windows.
pause
exit /b 0
:err
echo.
echo Setup FAILED. Read the error above.
pause
exit /b 1
