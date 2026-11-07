@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title 内网穿透

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 .venv\Scripts\python.exe
  pause
  exit /b 1
)

echo.
echo ==================================================
echo   第一步：先双击   分享给他人.bat   （它会占住 8080）
echo   第二步：回到这个窗口按任意键，开隧道
echo ==================================================
echo.
pause

".venv\Scripts\python.exe" -m tools.tunnel
echo.
echo 把上面打印的 https 地址 + ?code=口令 发给对方即可。
pause
