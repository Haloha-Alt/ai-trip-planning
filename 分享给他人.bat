@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM ================== 要改的就这三行 ==================
set WEB_HOST=0.0.0.0
set ACCESS_CODE=trip-8261
set PORT=8080
REM ===================================================
REM WEB_HOST  0.0.0.0 让局域网设备也能访问；只给自己用写 127.0.0.1
REM ACCESS_CODE 访问口令，对方第一次打开会被问一次；留空 = 不设防
REM PORT       必须和 cpolar 后台那条隧道的目标端口一致！
REM            你这台机器上 cpolar 隧道指向 8080，所以这里写 8080。
REM            若把隧道改成 8000，这里也跟着改成 8000。

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 .venv\Scripts\python.exe
  echo        请确认本文件在项目根目录，且虚拟环境已装好
  echo.
  pause
  exit /b 1
)

echo.
echo  正在以"可分享"方式启动（端口 %PORT%）
echo  口令：%ACCESS_CODE%
echo  关掉这个窗口 = 停止服务
echo.

".venv\Scripts\python.exe" -m api.server --port=%PORT%
set ERR=%errorlevel%

echo.
echo  服务已停止，退出码 %ERR%
pause
