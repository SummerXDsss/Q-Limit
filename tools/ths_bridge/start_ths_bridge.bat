@echo off
rem ============================================================
rem  同花顺下单客户端 中转服务（只读） 启动脚本
rem  前置条件：已安装并已登录「同花顺版下单客户端」(xiadan.exe)
rem  用法：双击本文件；或  start_ths_bridge.bat "D:\路径\xiadan.exe"
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "EXE=C:\同花顺软件\同花顺\xiadan.exe"

if not exist "%PY%" (
  echo [错误] 找不到虚拟环境：%PY%
  echo 请先执行：
  echo     py -3.11 -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt pywin32
  pause
  exit /b 1
)

if not "%~1"=="" set "EXE=%~1"

if not exist "%EXE%" (
  echo [警告] 没找到下单客户端：%EXE%
  echo         如果路径不对，请把 xiadan.exe 的实际路径作为参数传进来。
)

echo ============================================================
echo  正在启动同花顺中转服务（窗口请保持打开）
echo  下单客户端：%EXE%
echo ============================================================
"%PY%" "%~dp0ths_bridge.py" --exe "%EXE%"

echo.
echo 服务已退出（按任意键关闭）。
pause >nul
