@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM ================== 要改的就这两行 ==================
set WEB_HOST=0.0.0.0
set ACCESS_CODE=trip-8261
REM ===================================================
REM WEB_HOST=0.0.0.0  让同一个 Wi-Fi 下的设备也能访问；只给自己用就写 127.0.0.1
REM ACCESS_CODE        访问口令，对方第一次打开页面会被问一次；留空 = 不设防

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 .venv\Scripts\python.exe
  echo        请确认本文件在项目根目录，且虚拟环境已装好
  echo.
  pause
  exit /b 1
)

echo.
echo  正在以"可分享"方式启动
echo  口令：%ACCESS_CODE%
echo  关掉这个窗口 = 停止服务
echo.

".venv\Scripts\python.exe" -m api.server
set ERR=%errorlevel%

echo.
echo  服务已停止，退出码 %ERR%
pause
