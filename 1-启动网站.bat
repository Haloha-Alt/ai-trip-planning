@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AI 出行规划 · 本机

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 .venv\Scripts\python.exe
  echo        请确认本文件在项目根目录
  pause
  exit /b 1
)

echo.
echo ==================================================
echo   AI 出行规划 · 本机站点
echo   地址： http://127.0.0.1:8000/
echo   关掉这个窗口 = 停止服务
echo ==================================================
echo.
echo 正在打开浏览器……如果显示无法访问，等两秒按 F5。
start "" http://127.0.0.1:8000/
echo.

".venv\Scripts\python.exe" -m api.server
set ERR=%errorlevel%
echo.
echo 服务已停止，退出码 %ERR%
pause
