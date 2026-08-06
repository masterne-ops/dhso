@echo off
REM SO Data Analysis Platform - Windows Launcher

echo ==========================================
echo   SO Data Analysis - Starting...
echo ==========================================
echo.

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    echo Please run install.bat first
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
(
    echo [server]
    echo headless = true
    echo.
    echo [browser]
    echo gatherUsageStats = false
) > "%USERPROFILE%\.streamlit\config.toml"

if not exist "src\app.py" (
    echo [ERROR] src\app.py not found!
    pause
    exit /b 1
)

echo ==========================================
echo   Browser: http://localhost:8501
echo   Press Ctrl+C to stop
echo ==========================================
echo.
python -m streamlit run src\app.py --server.port 8501 --server.headless true

echo.
echo ==========================================
echo   Streamlit stopped
echo ==========================================
pause
