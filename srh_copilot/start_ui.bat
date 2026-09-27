@echo off
REM Window 2: starts the Streamlit chat UI. Run start_api.bat first.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo Starting UI on http://localhost:8501 (Ctrl+C to stop)
streamlit run frontend\streamlit_app.py
exit /b 0
