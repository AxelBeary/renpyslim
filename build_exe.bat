@echo off
REM Package RenPySlim into a single exe (activate .venv first).
REM Build recipe source of truth: RenPySlim.spec (release.yml uses the same file;
REM do not inline PyInstaller arguments here).
REM
REM NOTE: keep this file pure ASCII with CRLF line endings.
REM cmd.exe reads .bat as the OEM codepage (GBK/936 on zh-CN Windows); non-ASCII
REM bytes can swallow the following newline and turn comments into commands.
cd /d %~dp0
set TMP=%~dp0_tmp
set TEMP=%~dp0_tmp
if not exist "%TMP%" mkdir "%TMP%"
.venv\Scripts\python.exe -m PyInstaller --noconfirm RenPySlim.spec
echo.
echo Build finished. Artifact: dist\RenPySlim.exe
pause

