@echo off
chcp 936 >nul
title 停止手机打印服务
setlocal enabledelayedexpansion
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8760 " ^| findstr LISTENING') do (
  echo 正在结束进程 %%p
  taskkill /PID %%p /F >nul 2>&1
  set FOUND=1
)
if "!FOUND!"=="0" echo 服务当前没有在运行。
echo.
timeout /t 4
