@echo off
REM Window 1: checks the provider, rebuilds the index, starts the API.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python scripts\check_provider.py || goto :err
echo.
python scripts\ingest.py --agent student_service || goto :err
echo.
echo Starting API on http://localhost:8000 (Ctrl+C to stop)
uvicorn main:app --reload
exit /b 0
:err
echo.
echo Stopped. Fix the error above, most likely the GEMINI_API_KEY line in .env
pause
exit /b 1
