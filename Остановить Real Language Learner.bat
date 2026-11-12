@echo off
setlocal
cd /d "%~dp0"

docker compose down
if errorlevel 1 (
    echo Не удалось остановить приложение.
    pause
    exit /b 1
)

echo Приложение остановлено.
pause
