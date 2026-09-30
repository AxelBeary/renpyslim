@echo off
REM 打包 RenPySlim 为单个 exe（需先激活 .venv）
REM 构建配方唯一事实源：RenPySlim.spec（release.yml 同样消费该文件，勿在此重复内联参数）
cd /d %~dp0
set TMP=%~dp0_tmp
set TEMP=%~dp0_tmp
if not exist "%TMP%" mkdir "%TMP%"
.venv\Scripts\python.exe -m PyInstaller --noconfirm RenPySlim.spec
echo.
echo 打包完成，产物在 dist\RenPySlim.exe
pause
