@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Stop AI Photo Coach Backend
REM ============================================================
REM  Stop any python.exe listening on :8000.
REM  Normally just close the start-local-backend.cmd window;
REM  use this only if uvicorn is wedged.
REM ============================================================

set "PORT=8000"
echo.
echo [info] 查找占用端口 %PORT% 的进程...
echo.

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set "PID=%%P"
    if not "!PID!"=="0" (
        set "FOUND=1"
        echo [kill] PID=!PID!
        taskkill /F /PID !PID! >nul 2>&1
        if errorlevel 1 (
            echo        失败，可能权限不够。
        ) else (
            echo        已结束。
        )
    )
)

if "%FOUND%"=="" (
    echo [info] 端口 %PORT% 当前没有进程在监听。
)

echo.
pause
