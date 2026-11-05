@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  时迹 · 文件时间筛选器 - 一键打包
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python。请安装 Python 3.10+ 并勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

echo [1/4] 安装 / 检查依赖 ...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo       正在安装 PyInstaller ...
  python -m pip install pyinstaller
  if errorlevel 1 goto :fail
)

echo [2/4] 备份旧产物并清理 ...
if not exist dist_backup mkdir dist_backup
if exist "dist\时迹.exe" (
  for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%i
  copy /y "dist\时迹.exe" "dist_backup\时迹_旧包_%STAMP%.exe" >nul
  echo       旧包已备份到 dist_backup\
)
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "时迹.spec" del /q "时迹.spec"

echo [3/4] 使用 PyInstaller 打包为单个 exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "时迹" ^
  --icon "assets\app.ico" ^
  --add-data "web;web" ^
  --add-data "assets\app.ico;assets" ^
  --exclude-module PyQt5 --exclude-module PyQt6 ^
  --exclude-module PySide2 --exclude-module PySide6 ^
  --exclude-module matplotlib --exclude-module numpy --exclude-module pandas ^
  --exclude-module PIL --exclude-module IPython --exclude-module jupyter ^
  main.py
if errorlevel 1 goto :fail

echo [4/4] 检查产物 ...
if not exist "dist\时迹.exe" goto :fail
for %%f in ("dist\时迹.exe") do echo       生成：dist\时迹.exe  %%~zf 字节

echo.
echo ============================================
echo  打包完成：dist\时迹.exe
echo  双击即可运行（首次启动需解压，约 2-6 秒）
echo ============================================
pause
exit /b 0

:fail
echo.
echo [失败] 打包未完成，请查看上方错误信息。
pause
exit /b 1
