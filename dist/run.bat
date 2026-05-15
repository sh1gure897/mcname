@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  Minecraft Name Checker - easy launcher
REM  Just double-click this file in Explorer.
REM
REM  1) Paste your Discord webhook URL between the quotes below.
REM  2) Change LENGTH if you want (e.g. 3, 4, or "3 4").
REM ============================================================

set "WEBHOOK="
set "LENGTH=4"

REM %~dp0 = the folder this .bat lives in, so double-click works
REM no matter what the current directory is.
"%~dp0mc-name-checker.exe" --webhook "%WEBHOOK%" --length %LENGTH%

echo.
echo === Finished. Press any key to close this window. ===
pause >nul
