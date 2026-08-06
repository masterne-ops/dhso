@echo off
REM SO Data Analysis Platform - Windows One-Click Installer
REM Auto-installs Python if missing, uses Tsinghua mirror

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   SO Data Analysis Platform - Installer
echo ==========================================
echo.
echo This script will auto-install Python if needed.
echo All downloads use Tsinghua (China) mirror.
echo.

set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
set PIP_HOST=pypi.tuna.tsinghua.edu.cn

REM [1/10] Check files
echo [1/10] Checking project files...
if not exist "src\app.py" (
    echo [ERROR] src\app.py not found
    pause
    exit /b 1
)
echo [OK] Project files check passed
echo.

REM [2/10] Python
echo [2/10] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 goto :install_python
for /f "tokens=2" %%i in ('python --version') do set PYTHON_VER=%%i
echo [OK] Python %PYTHON_VER% found
goto :check_python_done

:install_python
set PYTHON_URL=https://mirrors.tuna.tsinghua.edu.cn/python/3.11.9/python-3.11.9-amd64.exe
echo [INFO] Downloading Python 3.11.9 from Tsinghua mirror...
certutil -urlcache -split -f "%PYTHON_URL%" "%TEMP%\python-installer.exe" >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%TEMP%\python-installer.exe'" 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Download failed. Install Python manually: https://www.python.org/
        pause
        exit /b 1
    )
)
echo [OK] Python installer downloaded
"%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
del "%TEMP%\python-installer.exe" 2>nul
for /f "delims=" %%a in ('powershell -Command "[Environment]::GetEnvironmentVariable('PATH','User')"') do set USER_PATH=%%a
set PATH=%PATH%;%USER_PATH%
timeout /t 5 /nobreak >nul
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python install failed. Restart cmd or install manually.
    pause
    exit /b 1
)
echo [OK] Python installed

:check_python_done
echo.

REM [3/10] pip
echo [3/10] Checking pip...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 python -m ensurepip --default-pip 2>nul
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip unavailable
    pause
    exit /b 1
)
echo [OK] pip available
echo.

REM [4/10] venv
echo [4/10] Creating virtual environment...
if exist "venv" rmdir /s /q "venv" 2>nul
python -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
)
echo [OK] Virtual environment created
echo.

REM [5/10] pip mirror
echo [5/10] Configuring pip mirror (Tsinghua)...
if not exist "%APPDATA%\pip" mkdir "%APPDATA%\pip" 2>nul
(
    echo [global]
    echo index-url = %PIP_INDEX%
    echo trusted-host = %PIP_HOST%
) > "%APPDATA%\pip\pip.ini"
echo [OK] Mirror configured
echo.

REM [6/10] Activate
echo [6/10] Activating venv...
call venv\Scripts\activate.bat
echo [OK] venv activated
echo.

REM [7/10] Upgrade pip
echo [7/10] Upgrading pip (Tsinghua)...
python -m pip install --upgrade pip -q --index-url %PIP_INDEX%
echo [OK] pip upgraded
echo.

REM [8/10] Install deps
echo [8/10] Installing dependencies...
echo  - streamlit / pandas / numpy / openpyxl / matplotlib
echo.
if exist "requirements.txt" (
    pip install -r requirements.txt --index-url %PIP_INDEX%
) else (
    pip install streamlit pandas numpy openpyxl matplotlib --index-url %PIP_INDEX%
)
if %errorlevel% neq 0 (
    echo [ERROR] Package installation failed
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM [9/10] Directories
echo [9/10] Creating directories...
if not exist "data"    mkdir data
if not exist "db"      mkdir db
if not exist "reports" mkdir reports
echo [OK] data / db / reports
echo.

REM [10/10] Streamlit config
echo [10/10] Configuring Streamlit...
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
(
    echo [server]
    echo headless = true
    echo.
    echo [browser]
    echo gatherUsageStats = false
) > "%USERPROFILE%\.streamlit\config.toml"
echo [OK] Streamlit configured
echo.

echo ==========================================
echo   Installation Complete!
echo ==========================================
echo.
echo [Start Application]
echo   Double-click: start.bat
echo.
echo [Browser]
echo   http://localhost:8501
echo.
echo [Usage]
echo   1. Main page "Data Import" - upload Excel
echo   2. Left sidebar to switch between modules
echo      - Product Flow Analysis (2025 vs 2026)
echo      - SO Mom Analysis (any 2 months)
echo   3. Batch export: python src\export_city_report.py
echo.
echo [Uninstall]
echo   Delete venv folder
echo.

pause
