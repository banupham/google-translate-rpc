@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo GOOGLE TRANSLATE LOCAL API V2
echo ===============================================

py -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo Dang cai requests...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Cai requests that bai.
        pause
        exit /b 1
    )
)

echo.
echo API: http://127.0.0.1:8080
echo Advanced mac dinh, Classic tuy chon.
echo Ctrl+C de dung.
echo.

py google_translate_api.py --host 127.0.0.1 --port 8080

pause
