@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Проверка Docker Desktop...
docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo Docker Desktop не запущен. Запускаю его...
    set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    if not exist "%DOCKER_DESKTOP%" (
        echo Docker Desktop не найден.
        echo Установите Docker Desktop и повторите попытку.
        pause
        exit /b 1
    )
    start "" "%DOCKER_DESKTOP%"
    echo Ожидание запуска Docker Desktop...
    for /l %%i in (1,1,60) do (
        docker info >nul 2>&1
        if not errorlevel 1 goto :docker_ready
        timeout /t 2 /nobreak >nul
    )
    echo Docker Desktop не успел запуститься.
    echo Запустите его вручную и повторите попытку.
    pause
    exit /b 1
)

:docker_ready
echo Запуск приложения...
docker compose up -d --build >nul 2>&1
if errorlevel 1 (
    echo.
    echo Не удалось запустить приложение.
    echo Подробности можно посмотреть командой:
    echo docker compose logs
    pause
    exit /b 1
)

echo Ожидание готовности сервера...
set "READY="
for /l %%i in (1,1,30) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$response = $null; try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/' -TimeoutSec 2 } catch {}; if ($response -and $response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { exit 0 } else { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set "READY=1"
        goto :ready
    )
    timeout /t 2 /nobreak >nul
)

echo.
echo Контейнер запущен, но сервер пока не ответил.
echo Откройте http://localhost:8000 позже или проверьте логи:
echo docker compose logs
pause
exit /b 1

:ready
echo Приложение готово: http://localhost:8000
start "" "http://localhost:8000/"
exit /b 0
