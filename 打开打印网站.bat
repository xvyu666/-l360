@echo off
cd /d %~dp0
curl.exe -s -o nul --max-time 2 http://127.0.0.1:8760/health
if not errorlevel 1 goto OPEN
echo 打印服务还没开，正在启动...
start "" "%~dp0启动打印服务.bat"
echo 等服务起来...
timeout /t 5 /nobreak >nul
:OPEN
start "" http://127.0.0.1:8760
