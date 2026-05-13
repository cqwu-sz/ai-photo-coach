@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title AI Photo Coach Backend [local]
REM ============================================================
REM  AI Photo Coach - local backend launcher
REM  - detect LAN IPv4
REM  - open Windows firewall on 8000
REM  - activate backend\.venv and start uvicorn 0.0.0.0:8000
REM  - log goes to this window; close window to stop
REM ============================================================

cd /d "%~dp0\.."
set "ROOT=%CD%"
set "BACKEND=%ROOT%\backend"
set "VENV=%BACKEND%\.venv"
set "PORT=8000"

echo.
echo ============================================================
echo  AI Photo Coach 本地后端
echo ============================================================
echo.

REM --- 1. 找本机 IPv4，优先 192/10/172 私网 ---
set "LAN_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4"') do (
    set "_ip=%%A"
    set "_ip=!_ip: =!"
    echo !_ip! | findstr /R /C:"^192\." /C:"^10\." /C:"^172\." >nul
    if not errorlevel 1 (
        if "!LAN_IP!"=="" set "LAN_IP=!_ip!"
    )
)
if "%LAN_IP%"=="" (
    for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4"') do (
        set "_ip=%%A"
        set "_ip=!_ip: =!"
        if "!LAN_IP!"=="" set "LAN_IP=!_ip!"
    )
)
if "%LAN_IP%"=="" set "LAN_IP=127.0.0.1"

echo [info] 局域网 IP   : %LAN_IP%
echo [info] 监听端口   : %PORT%
echo [info] iOS baseURL: http://%LAN_IP%:%PORT%
echo.

REM --- 2. 防火墙规则，没有就尝试加。失败不阻塞 ---
netsh advfirewall firewall show rule name="AIPhotoCoach Backend" >nul 2>&1
if errorlevel 1 (
    echo [info] 添加 Windows 防火墙规则 端口 %PORT% ，可能弹 UAC...
    netsh advfirewall firewall add rule name="AIPhotoCoach Backend" dir=in action=allow protocol=TCP localport=%PORT% >nul 2>&1
    if errorlevel 1 (
        echo [warn] 防火墙规则添加失败，可能需要管理员权限。
        echo        手动执行：
        echo        netsh advfirewall firewall add rule name=AIPhotoCoach^ Backend dir=in action=allow protocol=TCP localport=%PORT%
    ) else (
        echo [ok]   防火墙规则已添加。
    )
) else (
    echo [ok]   防火墙规则已存在。
)

REM --- 3. venv 检查 ---
if not exist "%VENV%\Scripts\activate.bat" (
    echo [error] 没找到 venv: %VENV%
    echo         先执行：
    echo           cd backend
    echo           python -m venv .venv
    echo           .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

REM --- 3.1 关键依赖快速自检：缺包就立刻装 ---
python -c "import jwt, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [info] venv 缺少关键依赖，自动 pip install -r requirements.txt ...
    pip install -q -r "%BACKEND%\requirements.txt"
    if errorlevel 1 (
        echo [error] 依赖安装失败，请手动执行：
        echo           "%VENV%\Scripts\pip" install -r "%BACKEND%\requirements.txt"
        pause
        exit /b 1
    )
)

REM --- 4. .env 检查 ---
if not exist "%BACKEND%\.env" (
    if exist "%BACKEND%\.env.example" (
        echo [info] 没找到 .env，从 .env.example 复制一份。
        copy /Y "%BACKEND%\.env.example" "%BACKEND%\.env" >nul
    ) else (
        echo [warn] 没找到 .env，也没有 .env.example，将使用默认配置。
    )
)

REM --- 5. 启动 uvicorn ---
echo.
echo ============================================================
echo  uvicorn 启动中... 关闭此窗口即可停止后端。
echo ============================================================
echo.
cd /d "%BACKEND%"
python -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% --log-level info --reload

echo.
echo [exit] uvicorn 已退出。按任意键关闭。
pause >nul
