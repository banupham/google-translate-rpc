@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo TEST GOOGLE TRANSLATE LOCAL API V2
echo ===============================================
echo.

echo [1] Health
curl -s "http://127.0.0.1:8080/health"
echo.
echo.

echo [2] Advanced
curl -s -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"hello world\",\"from\":\"en\",\"to\":\"vi\",\"mode\":\"advanced\"}"
echo.
echo.

echo [3] Classic
curl -s -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"hello world\",\"from\":\"en\",\"to\":\"vi\",\"mode\":\"classic\"}"
echo.
echo.

pause
