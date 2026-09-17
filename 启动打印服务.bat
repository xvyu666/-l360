@echo off
chcp 936 >nul
title 手机打印服务

cd /d "C:\Users\旭\WorkBuddy\2026-09-17-20-26-42\printserver"
start "" /MIN "C:\Users\旭\.workbuddy\binaries\python\envs\printserver\Scripts\python.exe" server.py
echo.
echo   手机打印服务已启动（窗口已最小化）
echo.
echo   手机连同一个 WiFi 后，浏览器打开：
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4" ') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo      http://%%b:8760
)
echo.
echo   关掉那个最小化的黑色窗口即可停止服务。
echo.
timeout /t 8
