@echo off
chcp 936 >nul
title 手机打印服务 - 首次安装

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo 正在申请管理员权限...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

cd /d "C:\Users\旭\WorkBuddy\2026-09-17-20-26-42\printserver"
"C:\Users\旭\.workbuddy\binaries\python\envs\printserver\Scripts\python.exe" setup.py
echo.
pause
