@echo off
REM 打包 RenPySlim 为单个 exe（需先激活 .venv）
cd /d %~dp0
set TMP=%~dp0_tmp
set TEMP=%~dp0_tmp
if not exist "%TMP%" mkdir "%TMP%"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed ^
  --name RenPySlim ^
  --icon assets\icon.ico ^
  --add-data "web\static;web/static" ^
  --add-data "assets;assets" ^
  --hidden-import rtools.pipeline ^
  --hidden-import rtools.packager ^
  --hidden-import pystray._win32 ^
  main.py
echo.
echo 打包完成，产物在 dist\RenPySlim.exe
pause
