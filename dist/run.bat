@echo off
chcp 65001 >nul
setlocal

REM Paste your Discord webhook URL between the quotes, then run.
REM LENGTH can be 3, 4, or "3 4" for both.

set "WEBHOOK="
set "LENGTH=4"

"%~dp0mc-name-checker.exe" --webhook "%WEBHOOK%" --length %LENGTH%

echo.
echo Done. Press any key to close.
pause >nul
